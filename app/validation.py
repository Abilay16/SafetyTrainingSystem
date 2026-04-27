# Валидация и санитизация входных данных

import re
import html
from typing import Optional
from fastapi import HTTPException

# Регулярные выражения для валидации
EMPNO_PATTERN = re.compile(r'^[a-zA-Z0-9_@.-]{1,50}$')
FIO_PATTERN = re.compile(r'^[а-яА-ЯёЁa-zA-Z\s\-]{1,200}$')
FILENAME_PATTERN = re.compile(r'^[a-zA-Z0-9_\-\.]{1,255}$')
MONTH_PATTERN = re.compile(r'^\d{4}-(0[1-9]|1[0-2])$')  # YYYY-MM

# Список опасных символов для SQL injection (дополнительная защита)
DANGEROUS_CHARS = ['--', ';--', '/*', '*/', '@@', '@']

def sanitize_html(text: str) -> str:
    """Экранировать HTML спецсимволы для защиты от XSS"""
    if not text:
        return text
    return html.escape(text)

def validate_empno(empno: str) -> str:
    """
    Валидация табельного номера/логина
    Допустимые символы: буквы, цифры, _, @, ., -
    """
    if not empno:
        raise HTTPException(400, "Табельный номер не может быть пустым")
    
    empno = empno.strip()
    
    if len(empno) > 50:
        raise HTTPException(400, "Табельный номер слишком длинный (макс 50 символов)")
    
    if not EMPNO_PATTERN.match(empno):
        raise HTTPException(
            400, 
            "Табельный номер содержит недопустимые символы. Используйте только буквы, цифры и символы: _ @ . -"
        )
    
    # Проверка на SQL injection паттерны
    for dangerous in DANGEROUS_CHARS:
        if dangerous in empno:
            raise HTTPException(400, "Недопустимые символы в табельном номере")
    
    return empno

def validate_fio(fio: str) -> str:
    """
    Валидация ФИО
    Допустимые символы: русские и английские буквы, пробелы, дефис
    """
    if not fio:
        raise HTTPException(400, "ФИО не может быть пустым")
    
    fio = fio.strip()
    
    if len(fio) < 2:
        raise HTTPException(400, "ФИО слишком короткое (минимум 2 символа)")
    
    if len(fio) > 200:
        raise HTTPException(400, "ФИО слишком длинное (макс 200 символов)")
    
    if not FIO_PATTERN.match(fio):
        raise HTTPException(
            400, 
            "ФИО содержит недопустимые символы. Используйте только буквы, пробелы и дефис"
        )
    
    # Экранируем HTML на всякий случай
    return sanitize_html(fio)

def validate_filename(filename: str) -> str:
    """
    Валидация имени файла
    Защита от directory traversal
    """
    if not filename:
        raise HTTPException(400, "Имя файла не может быть пустым")
    
    filename = filename.strip()
    
    # Проверка на directory traversal
    if '..' in filename or '/' in filename or '\\' in filename:
        raise HTTPException(400, "Недопустимые символы в имени файла")
    
    if len(filename) > 255:
        raise HTTPException(400, "Имя файла слишком длинное (макс 255 символов)")
    
    if not FILENAME_PATTERN.match(filename):
        raise HTTPException(
            400, 
            "Имя файла содержит недопустимые символы. Используйте только буквы, цифры, _, -, ."
        )
    
    return filename

def validate_month(month: str) -> str:
    """Валидация формата месяца YYYY-MM"""
    if not month:
        raise HTTPException(400, "Месяц не может быть пустым")
    
    month = month.strip()
    
    if not MONTH_PATTERN.match(month):
        raise HTTPException(400, "Неверный формат месяца. Используйте YYYY-MM")
    
    return month

def validate_instruction_type(instr_type: str) -> str:
    """Валидация типа инструктажа"""
    allowed_types = ['vvodny', 'pervichny', 'povtorny', 'vneplanovy', 'celevoy']
    
    if not instr_type:
        raise HTTPException(400, "Тип инструктажа не может быть пустым")
    
    instr_type = instr_type.strip().lower()
    
    if instr_type not in allowed_types:
        raise HTTPException(
            400, 
            f"Недопустимый тип инструктажа. Допустимые: {', '.join(allowed_types)}"
        )
    
    return instr_type

def validate_password(password: str) -> str:
    """
    Валидация пароля
    Требования: минимум 8 символов, хотя бы одна буква, одна цифра
    """
    if not password:
        raise HTTPException(400, "Пароль не может быть пустым")
    
    if len(password) < 8:
        raise HTTPException(400, "Пароль должен содержать минимум 8 символов")
    
    if len(password) > 128:
        raise HTTPException(400, "Пароль слишком длинный (макс 128 символов)")
    
    # Проверка на наличие букв и цифр
    has_letter = any(c.isalpha() for c in password)
    has_digit = any(c.isdigit() for c in password)
    
    if not has_letter or not has_digit:
        raise HTTPException(
            400, 
            "Пароль должен содержать хотя бы одну букву и одну цифру"
        )
    
    # Проверка на слабые пароли
    weak_passwords = [
        'password', '12345678', 'qwerty123', 'admin123', 
        'password123', '11111111', '00000000'
    ]
    
    if password.lower() in weak_passwords:
        raise HTTPException(400, "Слишком простой пароль. Выберите более надёжный")
    
    return password

def validate_positive_int(value: int, field_name: str = "значение") -> int:
    """Валидация положительного целого числа"""
    if value is None:
        raise HTTPException(400, f"{field_name} не может быть пустым")
    
    if not isinstance(value, int):
        raise HTTPException(400, f"{field_name} должно быть целым числом")
    
    if value < 0:
        raise HTTPException(400, f"{field_name} должно быть положительным числом")
    
    if value > 2147483647:  # max int32
        raise HTTPException(400, f"{field_name} слишком большое")
    
    return value

def sanitize_text_for_log(text: Optional[str], max_length: int = 500) -> Optional[str]:
    """
    Санитизация текста для записи в лог
    Удаляет чувствительные данные и ограничивает длину
    """
    if not text:
        return text
    
    # Удаляем возможные токены/пароли
    text = re.sub(r'Bearer\s+[\w\-\.]+', '[TOKEN]', text)
    text = re.sub(r'password["\']?\s*:\s*["\']?[\w\-\.]+', 'password: [HIDDEN]', text, flags=re.IGNORECASE)
    
    # Ограничиваем длину
    if len(text) > max_length:
        text = text[:max_length] + '...'
    
    return text
