```mermaid
flowchart TD
  S[/start] --> M[🏠 Главное меню (role-aware)]
  M --> B[✂️ Запись]
  M --> MB[📅 Мои записи]
  M --> C[📞 Контакты / ℹ️ О нас]
  M --> SUP[🆘 Поддержка]
  M --> ST[⚙️ Настройки]
  M --> PERS[👥 Персонал/роли (ACL)]
  M --> BR[📣 Рассылки]

  BR --> BR1[One-time]
  BR --> BR2[Сегменты]
  BR --> BR3[Dev tests]

  BR2 --> SG1[All clients]
  BR2 --> SG2[Active 30]
  BR2 --> SG3[Lost 30/60/90]
  BR2 --> SG4[No future booking]
  BR2 --> SG5[Cancelled booking]
  BR2 --> SG6[By master]
  BR2 --> SG7[By service/category]
  BR2 --> SG8[Birthday soon]

  MB --> CAN[Отмена записи]
  MB --> RESCH[Перенос записи]

  BR3 --> R48[48h confirmation test]
  BR3 --> R2[2h reminder test]

  N1[⬅️ Назад] --> PREV[Ровно один шаг назад]
  N2[🏠 Главное меню] --> M
```
