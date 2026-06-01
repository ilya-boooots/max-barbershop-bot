from app.services import booking_reminders as br


def test_48h_template_contains_required_fields():
    text = br._build_48h_text(client_name="Илья", master_name="Рената", service_name="Стрижка", visit_date="01.06.2026", visit_time="21:00", date_label="послезавтра")
    assert "Илья" in text and "Рената" in text and "01.06.2026" in text and "21:00" in text and "Стрижка" in text


def test_2h_template_has_master_and_no_promo_block():
    text = br._build_2h_text(client_name="Илья", service_name="Стрижка", visit_date="01.06.2026", visit_time="21:00", master_name="Рената", branch_address="Москва")

    assert all(x in text for x in ["Илья", "Стрижка", "01.06.2026", "21:00", "Рената", "Москва", "Ваш мастер:"])
    assert "Ваш барбер:" not in text
    assert "Специально для вас" not in text
    assert "скидка" not in text
    assert "подарочный сертификат" not in text


def test_2h_dev_test_payload_uses_same_template_rules():
    text = br._build_2h_text(
        client_name="Илья",
        service_name="МУЖСКАЯ СТРИЖКА",
        visit_date="01.06.2026",
        visit_time="21:00",
        master_name="Рената Пономарёва",
        branch_address="Саратов, улица Колотушкина 1",
    )

    assert "Ваш мастер:" in text
    assert "Ваш барбер:" not in text
    assert "Специально для вас" not in text
    assert "скидка" not in text
    assert "подарочный сертификат" not in text
