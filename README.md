# ABIR X6: управление после отказа WeBack

Практический способ вернуть дистанционное управление роботом-пылесосом ABIR X6,
который подключается к Wi-Fi, но постоянно отображается в WeBack как `Offline`.

Решение использует совместимое облако Redmond/grit-cloud и небольшой HTTP bridge,
работающий на постоянно включенном VPS. Bridge позволяет получить состояние
робота и отправлять основные команды из собственного приложения, Home Assistant,
скрипта или браузерной панели.

> Это не официальное решение ABIR, WeBack или Redmond. Используйте на свой риск.
> Не публикуйте учетные данные, API-токены и идентификатор своего робота.

## Краткий результат

Проверено на ABIR X6 с прошивкой `3.6.8`:

| Возможность | Результат |
|---|---|
| Проверка подключения, заряда и ошибки | Работает |
| Начать автоматическую уборку | Работает, пока робот не находится в глубоком сне |
| Остановить уборку | Работает |
| Вернуться на базу | Работает, пока робот бодрствует |
| Изменить мощность всасывания | Работает |
| Найти робота звуковым сигналом | Команда принимается, но на проверенном X6 звука нет |
| Разбудить из глубокого сна по сети | Не работает на проверенной прошивке |
| Карта и история уборки | Не реализованы |

Если робот уснул вне базы, его можно разбудить физической кнопкой или штатным ИК-пультом. После пробуждения сетевые команды снова работают.

## Что было неисправно

1. Робот создавал временную точку доступа `ROBOT`/`CCIT-ROBOT`.
2. Приложение передавало ему данные домашней сети 2.4 ГГц.
3. Робот успешно подключался к роутеру и имел доступ в интернет.
4. В WeBack устройство появлялось, но оставалось `Offline`.
5. Повторная привязка, смена страны, китайский аккаунт, VPN и старые версии приложения не возвращали управление.

Отсутствие интернета во временной сети `ROBOT` является нормальным: это локальная точка доступа для первоначальной настройки, а не интернет-сеть.

Диагностика показала, что проблема находится не в Wi-Fi и не в роутере. Старое облако/привязка WeBack больше не обеспечивает рабочий канал управления для этого устройства.

## Как устроено решение

```text
Android / Home Assistant / curl
              |
              | HTTPS + собственный Bearer token
              v
        HTTP bridge на VPS
              |
              | Redmond/grit-cloud API
              v
          AWS IoT shadow
              |
              v
            ABIR X6
```

Робот был заново привязан через совместимый процесс Redmond. После этого он появился в grit-cloud и начал отвечать на команды AWS IoT shadow:

- `thing_status_get` читает состояние;
- `send_to_device` публикует желаемое состояние;
- `working_status=AutoClean` запускает уборку;
- `working_status=Standby` останавливает;
- `working_status=BackCharging` отправляет на базу;
- `fan_status=Normal` и `fan_status=Strong` меняют мощность.

Bridge скрывает учетные данные облака, обновляет сессию и предоставляет простой локальный HTTP API.

## Важное ограничение глубокого сна

Статус `Hibernating` неоднозначен.

- На базе робот может оставаться доступным для команд.
- В глубоком сне вне базы Wi-Fi-модуль продолжает отвечать облаку, поэтому `connected=true`.
- Робот кратковременно подтверждает `AutoClean` или `BackCharging`, но двигатели не запускаются, и через несколько секунд статус снова становится `Hibernating`.
- Физическая кнопка и штатный ИК-пульт будят основной контроллер, после чего команды bridge снова работают.

Поэтому bridge не считает краткое появление `BackCharging` успехом: он повторно проверяет состояние через несколько секунд и сообщает ошибку, если робот снова уснул.

## Требования

- ABIR X6 или совместимый робот, уже видимый в Redmond/grit-cloud;
- Linux VPS с Python 3;
- учетная запись Redmond, к которой привязан робот;
- `thing_name` и `sub_type` робота;
- случайный длинный API-токен для защиты bridge;
- опционально Caddy/Nginx для HTTPS.

## Поиск идентификатора робота

```bash
export REDMOND_ACCOUNT='your-email@example.com'
export REDMOND_PASSWORD='your-password'
export REDMOND_CALLING_CODE='65'
python3 discover_devices.py
```

Скрипт выводит список устройств. Не публикуйте полный результат: `thing_name` является уникальным идентификатором вашего робота.

## Установка bridge на VPS

```bash
sudo mkdir -p /opt/abir-x6-bridge
sudo cp robot_control_bridge.py /opt/abir-x6-bridge/
sudo cp systemd/abir-x6-bridge.service /etc/systemd/system/
sudo cp systemd/abir-x6-bridge.env.example /etc/abir-x6-bridge.env
sudo chmod 600 /etc/abir-x6-bridge.env
sudo nano /etc/abir-x6-bridge.env
sudo systemctl daemon-reload
sudo systemctl enable --now abir-x6-bridge
```

Не открывайте порт `18081` напрямую в интернет. Оставьте bridge на `127.0.0.1` и используйте HTTPS reverse proxy, VPN или SSH-туннель.

## HTTP API

```bash
curl -H "Authorization: Bearer YOUR_API_TOKEN" https://your-host.example/status
curl -X POST -H "Authorization: Bearer YOUR_API_TOKEN" -H "Content-Type: application/json" -d '{"command":"clean"}' https://your-host.example/command
```

Команды: `clean`, `stop`, `dock`, `fan_normal`, `fan_strong`, `locate`.

## Почему не получилось получить карту

Основные команды и состояние передаются через shadow API. Карта, маршрут и история уборки используют отдельные сообщения/хранилище. Для реализации живой карты потребуется отдельный реверс-инжиниринг протокола или доступ к внутренней плате робота.

## Безопасность

- Никогда не добавляйте заполненный `.env` в Git.
- Используйте отдельный длинный `ROBOT_API_TOKEN`.
- Публикуйте API только через HTTPS.
- Ограничьте доступ VPN, firewall или IP allowlist.
- Считайте `thing_name` и учетные данные конфиденциальными.

## English summary

This repository documents a practical workaround for an ABIR X6 that connects to Wi-Fi but remains offline in WeBack. After rebinding the robot to a compatible Redmond/grit-cloud account, the included VPS bridge exposes a small authenticated HTTP API for status, cleaning, stopping, docking and fan control.

Deep sleep remains a firmware limitation: the cloud connection stays online, but the main controller ignores motor commands until the physical button or original IR remote wakes the robot.
