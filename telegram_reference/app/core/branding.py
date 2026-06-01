from __future__ import annotations

from app.core.config import get_settings


def business_name() -> str:
    return get_settings().business_name


def support_contact() -> str:
    return get_settings().support_contact


def support_text() -> str:
    return f"🆘 Если у вас возникли вопросы, напишите нам {support_contact()} — с удовольствием поможем! 🙂"


def start_greeting() -> str:
    return f"👋 Добро пожаловать в {business_name()}! Рады помочь с визитом ✂️"


def about_text() -> str:
    return (
        f"💈 О нас\n\n{business_name()} — место, где заботятся о вашем стиле ✨\n"
        "Если нужна помощь с записью, напишите в поддержку 📨"
    )


def contacts_text() -> str:
    settings = get_settings()
    return (
        f"📍 Контакты {business_name()}\n\n"
        f"🏠 Адрес: {settings.business_address}\n"
        f"📞 Телефон: {settings.business_phone}"
    )


def admin_client_contact_template(client_name: str) -> str:
    return (
        f"Здравствуйте, {client_name}! Это {business_name()} 💈\n"
        "Пишем по вашей записи. Если нужно уточнить детали или перенести визит — "
        "ответьте на это сообщение, пожалуйста 🙂"
    )


def clients_directory_template(client_id: str, query: str | None) -> str:
    return (
        "📋 Шаблон сообщения\n\n"
        f"Здравствуйте! Это {business_name()} 💈\n"
        "Хотим напомнить о визите и помочь с удобным временем 🙂\n"
        f"ID клиента: {client_id}\n"
        f"Запрос: {query or '—'}"
    )
