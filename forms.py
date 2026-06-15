from flask_wtf import FlaskForm
from wtforms import StringField, TextAreaField, IntegerField, SelectMultipleField, FileField, SelectField, BooleanField
from wtforms.validators import DataRequired, NumberRange, Optional

class BookForm(FlaskForm):
    title = StringField('Название', validators=[DataRequired()])
    description = TextAreaField('Описание', validators=[DataRequired()])
    year = IntegerField('Год', validators=[DataRequired(), NumberRange(min=0, max=2026)])
    publisher = StringField('Издательство', validators=[DataRequired()])
    author = StringField('Автор', validators=[DataRequired()])
    pages = IntegerField('Объём (страницы)', validators=[DataRequired(), NumberRange(min=1)])
    genres = SelectMultipleField('Жанры', coerce=int, validators=[Optional()])
    cover = FileField('Обложка', validators=[Optional()])

class ReviewForm(FlaskForm):
    rating = SelectField('Оценка', choices=[
        (5, '5 – отлично'),
        (4, '4 – хорошо'),
        (3, '3 – удовлетворительно'),
        (2, '2 – неудовлетворительно'),
        (1, '1 – плохо'),
        (0, '0 – ужасно')
    ], coerce=int, validators=[DataRequired()])
    text = TextAreaField('Текст рецензии', validators=[DataRequired()])

class LoginForm(FlaskForm):
    login = StringField('Логин', validators=[DataRequired()])
    password = StringField('Пароль', validators=[DataRequired()])
    remember = BooleanField('Запомнить меня')