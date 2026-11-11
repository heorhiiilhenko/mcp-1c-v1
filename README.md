# MCP сервер с RAG описанием структуры конфигурации 1С

Демонстрация запуска и использования на Youtube:
[![Демонстрация запуска и использования MCP сервера для разработки 1С](https://i.ytimg.com/vi/74kYcK6bvGk/sd3.jpg)](http://www.youtube.com/watch?v=74kYcK6bvGk)

[**Подробнее в статье**](https://github.com/FSerg/mcp-1c-v1/blob/main/article/article.md    ).

Проект состоит из нескольких сервисов для работы с векторной базой данных на основе документации 1C:

1. **Embedding Service** - сервис генерации векторных представлений
2. **ПолучитьТекстСтруктурыКонфигурацииФайлами.epf** - обработка для выгрузки структуры конфигурации 1С
3. **Loader** - веб-интерфейс для загрузки архива выгрузки из 1С в векторную БД Qdrant
4. **MCP Server** - сервер для ответов на вопросы с использованием RAG

## Новые возможности

### 🚀 Мультивекторный поиск

MCP сервер теперь поддерживает мультивекторный поиск с использованием RRF (Reciprocal Rank Fusion):

- **Два векторных представления**: `object_name` и `friendly_name` для каждого объекта
- **RRF алгоритм**: автоматическое объединение результатов поиска по разным векторам
- **Улучшенная точность**: более релевантные результаты поиска
- **Backward compatibility**: возможность отключить мультивекторный поиск

📖 [Подробнее о мультивекторном поиске](mcp/MULTIVECTOR_SEARCH.md)

## Быстрый запуск

### Запуск всех сервисов

```bash
chmod +x start.sh && chmod +x stop.sh
# Запуск всех сервисов
./start.sh

# Или вручную
docker-compose up --build
```

### Остановка сервисов

```bash
# Остановка всех сервисов
./stop.sh

# Или вручную
docker-compose down
```

### Автозапуск при старті Ubuntu

systemd-юніт `mcp-1c-v1.service` при завантаженні системи запускає `./start.sh`
(основний стек, `docker compose up --build -d`), а після нього — `embeddings/`
(він підключається до мережі `mcp-1c-v1_mcp-network`, тож мусить іти другим).
Зупинка — навпаки: спершу `embeddings/`, потім `./stop.sh`.

```bash
sudo ./install-autostart.sh            # встановити в /etc/systemd/system і увімкнути
sudo ./install-autostart.sh --remove   # вимкнути автозапуск (контейнери не чіпає)

systemctl status mcp-1c-v1             # стан
sudo systemctl restart mcp-1c-v1       # stop.sh + start.sh (і embeddings)
journalctl -u mcp-1c-v1 -b             # лог запуску в поточному завантаженні
```

Шаблон юніта — `systemd/mcp-1c-v1.service` (`@USER@`/`@DIR@` підставляє
інсталятор). Користувач має бути в групі `docker`.


## Доступные сервисы

После запуска будут доступны:

- **Loader (Streamlit)**: http://localhost:8501 - веб-интерфейс для загрузки файлов
- **Embedding Service**: http://localhost:5000 - API для генерации эмбеддингов
- **Qdrant**: http://localhost:6333/dashboard - векторная база данных
- **MCP Server**: http://localhost:8000/mcp - сервер для ответов на вопросы
- **MCP Inspector**: http://localhost:6274 - веб-интерфейс проверки/отладки MCP серверов

## Структура проекта

```
mcp-1c-v1/
├── embeddings/           # Сервис генерации эмбеддингов
│   ├── Dockerfile
│   ├── embedding_service.py
│   ├── config.json
│   └── requirements.txt
├── loader/              # Веб-интерфейс для загрузки данных
│   ├── Dockerfile
│   ├── loader.py
│   ├── config.py
│   └── requirements.txt
├── mcp/                 # MCP 1С RAG сервер
│   ├── Dockerfile
│   ├── mcp_server.py
│   ├── config.py
│   └── requirements.txt
├── inspector/           # MCP Inspector
│   ├── Dockerfile
│   ├── mcp_server.py
│   ├── config.py
│   └── requirements.txt
├── ПолучитьТекстСтруктурыКонфигурацииФайлами.epf # Обработка для 1С для выгрузки структуры
├── docker-compose.yml  # Конфигурация всех сервисов
├── start.sh            # Скрипт запуска
├── stop.sh             # Скрипт остановки
├── install-autostart.sh # Автозапуск при старте системы (systemd)
└── systemd/            # Шаблон юнита mcp-1c-v1.service
```

## Использование

1. Запустите все сервисы: `./start.sh`
2. Выгрузите описание структуры конфигурации из 1С: `ПолучитьТекстСтруктурыКонфигурацииФайлами.epf`
3. Откройте веб-интерфейс загрузчика: http://youraddress:8501
4. Загрузите ZIP-архив с markdown файлами и файлом objects.csv
5. Нажмите "Начать обработку"
6. После загрузки данные будут доступны в Qdrant для поиска через MCP Server

Для агентов (Cursor, RooCode) поддерживающих современный протокол Streamable HTTP, указываем: http://youaddress:8000/mcp
Для VSCode Copilot, хоть и заявлена поддержка Streamable HTTP, но у меня работает только как SSE, поэтому указывал: http://youraddress:8000/mcp/sse

**Для VSCode Copilot .../YourProject/.vscode/mcp.json**
```json
{
    "servers": {
        "my-1c-mcp-server": {
            "headers": {
                "x-collection-name": "my_vector_collection"
            },
            "url": "http://youraddress:8000/mcp/sse"
        }
    }
}
```

**Для Cursor .../YourProject/.cursor/mcp.json**
```json
{
    "servers": {
        "my-1c-mcp-server": {
            "headers": {
                "x-collection-name": "my_vector_collection"
            },
            "url": "http://youraddress:8000/mcp"
        }
    }
}
```
Через заголовок `x-collection-name` можно указать имя коллекции в Qdrant вместо дефолтного `1c_rag`. Т.к. настройки MCP почти везде можно указывать на уровне проекта, то это позволяет один и тот же инстанс MCP сервера использовать для разных проектов с разными коллекциями (для разных конфигураций 1С).

## Переменные окружения

Можно настроить через переменные окружения:

- `EMBEDDING_SERVICE_URL` - URL сервиса эмбеддингов (по умолчанию: http://youraddress:5000)
- `QDRANT_HOST` - хост Qdrant (по умолчанию: localhost)
- `QDRANT_PORT` - порт Qdrant (по умолчанию: 6333)
- `COLLECTION_NAME` - имя коллекции в Qdrant (по умолчанию: 1c_rag), который может быть переопределено в заголовке `x-collection-name` в настройках подключения MCP сервера
- `ROW_BATCH_SIZE` - размер батча строк (по умолчанию: 250)
- `EMBEDDING_BATCH_SIZE` - размер батча эмбеддингов (по умолчанию: 50)

## Отладка

Для просмотра логов отдельного сервиса:

```bash
# Логи конкретного сервиса
docker-compose logs -f loader
docker-compose logs -f embedding-service
docker-compose logs -f qdrant
docker-compose logs -f mcp-server

# Логи всех сервисов
docker-compose logs -f
```
