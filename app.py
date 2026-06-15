import os
import hashlib
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, flash, abort, send_file, session
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from sqlalchemy import func
import bleach
import markdown
import csv
from io import StringIO, BytesIO

from models import db, User, Role, Book, Genre, Cover, Review, BookViewLog
from forms import LoginForm, BookForm, ReviewForm

app = Flask(__name__)

# Конфигурация
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                                    'instance', 'library.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'uploads')
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

# Создаём необходимые папки
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'instance'), exist_ok=True)

db.init_app(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.login_message = 'Для выполнения данного действия необходимо пройти процедуру аутентификации'
login_manager.login_message_category = 'warning'


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


# Контекстный процессор для ролей
@app.context_processor
def utility_processor():
    def user_has_role(role_name):
        if current_user.is_authenticated and current_user.role:
            return current_user.role.name == role_name
        return False

    return dict(user_has_role=user_has_role)


# Вспомогательные функции
def save_cover(file, book_id):
    if not file or file.filename == '':
        return None

    file_data = file.read()
    md5_hash = hashlib.md5(file_data).hexdigest()
    existing = Cover.query.filter_by(md5_hash=md5_hash).first()
    if existing:
        return existing.id

    ext = file.filename.rsplit('.', 1)[1].lower()
    filename = f"{md5_hash}.{ext}"
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    with open(filepath, 'wb') as f:
        f.write(file_data)

    cover = Cover(filename=filename, mime_type=file.mimetype, md5_hash=md5_hash, book_id=book_id)
    db.session.add(cover)
    db.session.commit()
    return cover.id


def render_markdown(text):
    clean_text = bleach.clean(text, strip=True)
    return markdown.markdown(clean_text, extensions=['extra', 'codehilite'])


def log_book_view(book_id):
    user_id = current_user.id if current_user.is_authenticated else None
    today = datetime.now().date()
    today_start = datetime(today.year, today.month, today.day)
    today_end = today_start + timedelta(days=1)

    views_today = BookViewLog.query.filter(
        BookViewLog.book_id == book_id,
        BookViewLog.user_id == user_id,
        BookViewLog.viewed_at >= today_start,
        BookViewLog.viewed_at < today_end
    ).count()

    if views_today < 10:
        log = BookViewLog(book_id=book_id, user_id=user_id)
        db.session.add(log)
        db.session.commit()


def get_average_rating(book_id):
    result = db.session.query(func.avg(Review.rating)).filter_by(book_id=book_id).scalar()
    return round(result, 1) if result else 0


def get_reviews_count(book_id):
    return Review.query.filter_by(book_id=book_id).count()


# Маршруты
@app.route('/')
def index():
    page = request.args.get('page', 1, type=int)
    books = Book.query.order_by(Book.year.desc()).paginate(page=page, per_page=10, error_out=False)

    three_months_ago = datetime.now() - timedelta(days=90)
    popular_books = db.session.query(
        Book, func.count(BookViewLog.id).label('views')
    ).join(BookViewLog, Book.id == BookViewLog.book_id) \
        .filter(BookViewLog.viewed_at >= three_months_ago) \
        .group_by(Book.id).order_by(func.count(BookViewLog.id).desc()).limit(5).all()

    recent_books = []
    if current_user.is_authenticated:
        recent_logs = BookViewLog.query.filter_by(user_id=current_user.id) \
            .order_by(BookViewLog.viewed_at.desc()).limit(5).all()
        recent_books = [log.book for log in recent_logs if log.book]
    else:
        recent_ids = session.get('recent_books', [])
        if recent_ids:
            recent_books = Book.query.filter(Book.id.in_(recent_ids)).all()
            recent_books.sort(key=lambda x: recent_ids.index(x.id) if x.id in recent_ids else 999)

    return render_template('index.html',
                           books=books,
                           popular_books=popular_books,
                           recent_books=recent_books,
                           get_average_rating=get_average_rating,
                           get_reviews_count=get_reviews_count)


@app.route('/book/<int:book_id>')
def book_detail(book_id):
    book = Book.query.get_or_404(book_id)
    log_book_view(book_id)

    if not current_user.is_authenticated:
        recent = session.get('recent_books', [])
        if book_id in recent:
            recent.remove(book_id)
        recent.insert(0, book_id)
        session['recent_books'] = recent[:5]

    avg_rating = get_average_rating(book_id)
    reviews = Review.query.filter_by(book_id=book_id).order_by(Review.created_at.desc()).all()

    user_review = None
    if current_user.is_authenticated:
        user_review = Review.query.filter_by(book_id=book_id, user_id=current_user.id).first()

    can_review = current_user.is_authenticated and not user_review

    return render_template('book_detail.html',
                           book=book,
                           avg_rating=avg_rating,
                           reviews=reviews,
                           user_review=user_review,
                           can_review=can_review,
                           render_markdown=render_markdown)


@app.route('/book/add', methods=['GET', 'POST'])
@login_required
def add_book():
    if current_user.role.name != 'администратор':
        flash('У вас недостаточно прав для выполнения данного действия', 'danger')
        return redirect(url_for('index'))

    form = BookForm()
    form.genres.choices = [(g.id, g.name) for g in Genre.query.all()]

    if form.validate_on_submit():
        try:
            book = Book(
                title=form.title.data,
                description=bleach.clean(form.description.data),
                year=form.year.data,
                publisher=form.publisher.data,
                author=form.author.data,
                pages=form.pages.data
            )
            db.session.add(book)
            db.session.flush()

            for genre_id in form.genres.data:
                genre = Genre.query.get(genre_id)
                if genre:
                    book.genres.append(genre)

            db.session.commit()

            if form.cover.data:
                save_cover(form.cover.data, book.id)

            flash('Книга успешно добавлена', 'success')
            return redirect(url_for('book_detail', book_id=book.id))
        except Exception as e:
            db.session.rollback()
            flash(f'При сохранении данных возникла ошибка: {str(e)}', 'danger')

    return render_template('book_form.html', form=form, book=None, title='Добавление книги')


@app.route('/book/edit/<int:book_id>', methods=['GET', 'POST'])
@login_required
def edit_book(book_id):
    book = Book.query.get_or_404(book_id)

    if current_user.role.name not in ['администратор', 'модератор']:
        flash('У вас недостаточно прав для выполнения данного действия', 'danger')
        return redirect(url_for('index'))

    form = BookForm()
    form.genres.choices = [(g.id, g.name) for g in Genre.query.all()]

    if form.validate_on_submit():
        try:
            book.title = form.title.data
            book.description = bleach.clean(form.description.data)
            book.year = form.year.data
            book.publisher = form.publisher.data
            book.author = form.author.data
            book.pages = form.pages.data

            book.genres = []
            for genre_id in form.genres.data:
                genre = Genre.query.get(genre_id)
                if genre:
                    book.genres.append(genre)

            db.session.commit()
            flash('Книга успешно обновлена', 'success')
            return redirect(url_for('book_detail', book_id=book.id))
        except Exception as e:
            db.session.rollback()
            flash(f'При сохранении данных возникла ошибка: {str(e)}', 'danger')
    else:
        form.title.data = book.title
        form.description.data = book.description
        form.year.data = book.year
        form.publisher.data = book.publisher
        form.author.data = book.author
        form.pages.data = book.pages
        form.genres.data = [g.id for g in book.genres]

    return render_template('book_form.html', form=form, book=book, title='Редактирование книги')


@app.route('/book/delete/<int:book_id>', methods=['POST'])
@login_required
def delete_book(book_id):
    if current_user.role.name != 'администратор':
        flash('У вас недостаточно прав для выполнения данного действия', 'danger')
        return redirect(url_for('index'))

    book = Book.query.get_or_404(book_id)
    title = book.title

    if book.cover:
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], book.cover.filename)
        if os.path.exists(filepath):
            os.remove(filepath)

    db.session.delete(book)
    db.session.commit()
    flash(f'Книга "{title}" успешно удалена', 'success')
    return redirect(url_for('index'))


@app.route('/review/add/<int:book_id>', methods=['GET', 'POST'])
@login_required
def add_review(book_id):
    book = Book.query.get_or_404(book_id)

    existing = Review.query.filter_by(book_id=book_id, user_id=current_user.id).first()
    if existing:
        flash('Вы уже оставили рецензию на эту книгу', 'warning')
        return redirect(url_for('book_detail', book_id=book_id))

    form = ReviewForm()

    if form.validate_on_submit():
        try:
            review = Review(
                book_id=book_id,
                user_id=current_user.id,
                rating=form.rating.data,
                text=bleach.clean(form.text.data)
            )
            db.session.add(review)
            db.session.commit()
            flash('Рецензия успешно добавлена', 'success')
            return redirect(url_for('book_detail', book_id=book_id))
        except Exception as e:
            db.session.rollback()
            flash(f'Ошибка при сохранении рецензии: {str(e)}', 'danger')

    return render_template('review_form.html', form=form, book=book)


@app.route('/review/delete/<int:review_id>', methods=['POST'])
@login_required
def delete_review(review_id):
    if current_user.role.name not in ['администратор', 'модератор']:
        flash('У вас недостаточно прав для выполнения данного действия', 'danger')
        return redirect(url_for('index'))

    review = Review.query.get_or_404(review_id)
    book_id = review.book_id
    db.session.delete(review)
    db.session.commit()
    flash('Рецензия успешно удалена', 'success')
    return redirect(url_for('book_detail', book_id=book_id))


@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('index'))

    form = LoginForm()

    if form.validate_on_submit():
        user = User.query.filter_by(login=form.login.data).first()
        if user and user.check_password(form.password.data):
            login_user(user, remember=form.remember.data)
            next_page = request.args.get('next')
            flash(f'Добро пожаловать, {user.full_name}!', 'success')
            return redirect(next_page) if next_page else redirect(url_for('index'))
        flash('Невозможно аутентифицироваться с указанными логином и паролем', 'danger')

    return render_template('login.html', form=form)


@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('Вы успешно вышли из системы', 'info')
    return redirect(url_for('index'))


@app.route('/statistics')
@login_required
def statistics():
    if current_user.role.name != 'администратор':
        flash('У вас недостаточно прав для выполнения данного действия', 'danger')
        return redirect(url_for('index'))

    page_actions = request.args.get('page_actions', 1, type=int)
    page_stats = request.args.get('page_stats', 1, type=int)

    actions_log = BookViewLog.query.order_by(BookViewLog.viewed_at.desc()).paginate(
        page=page_actions, per_page=10, error_out=False
    )

    date_from = request.args.get('date_from')
    date_to = request.args.get('date_to')

    query = db.session.query(
        Book, func.count(BookViewLog.id).label('views')
    ).join(BookViewLog, Book.id == BookViewLog.book_id) \
        .filter(BookViewLog.user_id.isnot(None))

    if date_from:
        try:
            date_from_obj = datetime.strptime(date_from, '%Y-%m-%d')
            query = query.filter(BookViewLog.viewed_at >= date_from_obj)
        except ValueError:
            pass

    if date_to:
        try:
            date_to_obj = datetime.strptime(date_to, '%Y-%m-%d') + timedelta(days=1)
            query = query.filter(BookViewLog.viewed_at < date_to_obj)
        except ValueError:
            pass

    stats = query.group_by(Book.id).order_by(func.count(BookViewLog.id).desc()).paginate(
        page=page_stats, per_page=10, error_out=False
    )

    return render_template('statistics.html',
                           actions_log=actions_log,
                           stats=stats,
                           date_from=date_from,
                           date_to=date_to)


@app.route('/export/<string:export_type>')
@login_required
def export_csv(export_type):
    if current_user.role.name != 'администратор':
        flash('У вас недостаточно прав', 'danger')
        return redirect(url_for('index'))

    date_from = request.args.get('date_from')
    date_to = request.args.get('date_to')

    output = StringIO()
    writer = csv.writer(output, delimiter=';')

    if export_type == 'actions':
        writer.writerow(['№', 'ФИО пользователя', 'Название книги', 'Дата и время просмотра'])
        logs = BookViewLog.query.order_by(BookViewLog.viewed_at.desc()).all()
        for i, log in enumerate(logs, 1):
            fio = log.user.full_name if log.user else 'Неаутентифицированный пользователь'
            book_title = log.book.title if log.book else 'Книга удалена'
            writer.writerow([i, fio, book_title, log.viewed_at.strftime('%Y-%m-%d %H:%M:%S')])
        filename = f'journal_actions_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv'

    elif export_type == 'stats':
        writer.writerow(['№', 'Название книги', 'Количество просмотров'])
        query = db.session.query(
            Book, func.count(BookViewLog.id).label('views')
        ).join(BookViewLog, Book.id == BookViewLog.book_id) \
            .filter(BookViewLog.user_id.isnot(None))

        if date_from:
            try:
                date_from_obj = datetime.strptime(date_from, '%Y-%m-%d')
                query = query.filter(BookViewLog.viewed_at >= date_from_obj)
            except ValueError:
                pass

        if date_to:
            try:
                date_to_obj = datetime.strptime(date_to, '%Y-%m-%d') + timedelta(days=1)
                query = query.filter(BookViewLog.viewed_at < date_to_obj)
            except ValueError:
                pass

        stats_data = query.group_by(Book.id).order_by(func.count(BookViewLog.id).desc()).all()
        for i, (book, views) in enumerate(stats_data, 1):
            writer.writerow([i, book.title, views])
        filename = f'book_stats_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv'
    else:
        abort(404)

    output.seek(0)
    output_bytes = output.getvalue().encode('utf-8-sig')

    return send_file(
        BytesIO(output_bytes),
        mimetype='text/csv',
        as_attachment=True,
        download_name=filename
    )


# Инициализация базы данных
def init_db():
    with app.app_context():
        db.create_all()

        if Role.query.count() == 0:
            roles = [
                Role(name='администратор', description='Имеет полный доступ к системе'),
                Role(name='модератор', description='Может редактировать книги и удалять рецензии'),
                Role(name='пользователь', description='Может оставлять рецензии')
            ]
            db.session.add_all(roles)
            db.session.commit()

        if Genre.query.count() == 0:
            genres = ['Фантастика', 'Детектив', 'Роман', 'Поэзия', 'Научная литература', 'Приключения']
            for g in genres:
                db.session.add(Genre(name=g))
            db.session.commit()

        if not User.query.filter_by(login='admin').first():
            admin_role = Role.query.filter_by(name='администратор').first()
            admin = User(login='admin', last_name='Иванов', first_name='Иван', patronymic='Иванович',
                         role_id=admin_role.id)
            admin.set_password('admin123')
            db.session.add(admin)
            db.session.commit()

        if not User.query.filter_by(login='moderator').first():
            mod_role = Role.query.filter_by(name='модератор').first()
            moderator = User(login='moderator', last_name='Петров', first_name='Петр', role_id=mod_role.id)
            moderator.set_password('mod123')
            db.session.add(moderator)
            db.session.commit()

        if not User.query.filter_by(login='user').first():
            user_role = Role.query.filter_by(name='пользователь').first()
            user = User(login='user', last_name='Сидоров', first_name='Сидор', role_id=user_role.id)
            user.set_password('user123')
            db.session.add(user)
            db.session.commit()


if __name__ == '__main__':
    init_db()
    app.run(debug=True)