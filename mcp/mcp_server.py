import os
from fastmcp import FastMCP
from fastmcp.server.dependencies import get_http_headers
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
import json
import requests
from qdrant_client import QdrantClient
from qdrant_client.models import Filter, Prefetch, FusionQuery, Fusion
from typing import Dict, Any, List, Literal
from pydantic import BaseModel, Field

from config import (
    QDRANT_HOST, QDRANT_PORT, COLLECTION_NAME, SYNTAX_COLLECTION_NAME,
    PLATFORM_COLLECTION_NAME, CODE_COLLECTION_NAME, UTP_CODE_COLLECTION_NAME,
    EMBEDDING_SERVICE_URL,
    SERVER_HOST, SERVER_PORT, DEFAULT_SEARCH_LIMIT, MAX_SEARCH_LIMIT,
    MIN_SEARCH_LIMIT, SERVER_NAME,
    EMBEDDING_REQUEST_TIMEOUT, HEALTH_CHECK_TIMEOUT,
    OBJECT_NAME_VECTOR, FRIENDLY_NAME_VECTOR, PREFETCH_LIMIT_MULTIPLIER
)

mcp = FastMCP(name=SERVER_NAME)

# Подключение к Qdrant
qdrant_client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)


class SearchRequest(BaseModel):
    """Модель запроса для поиска в документации 1С"""
    query: str = Field(
        description="Наименование объекта конфигурации или часть его имени для поиска в документации 1С",
        min_length=1,
        max_length=500
    )
    object_type: Literal["Справочник", "Документ", "РегистрСведений", "РегистрНакопления",
                         "Константа", "Перечисление", "ПланВидовХарактеристик"] | None = Field(
        default=None,
        description="Фильтр по типу объекта конфигурации 1С. Если не указан, поиск выполняется по всем типам объектов"
    )
    limit: int = Field(
        default=DEFAULT_SEARCH_LIMIT,
        description=f"Максимальное количество результатов поиска (по умолчанию {DEFAULT_SEARCH_LIMIT})",
        ge=MIN_SEARCH_LIMIT,
        le=MAX_SEARCH_LIMIT
    )
    use_multivector: bool = Field(
        default=True,
        description="Использовать мультивекторный поиск с RRF для более точного ранжирования результатов"
    )


class SearchRequestMCP(BaseModel):
    """Модель запроса для поиска в документации 1С"""
    query: str = Field(
        description="Наименование объекта конфигурации или часть его имени для поиска в документации 1С",
        min_length=1,
        max_length=500
    )
    object_type: Literal["Справочник", "Документ", "РегистрСведений", "РегистрНакопления",
                         "Константа", "Перечисление", "ПланВидовХарактеристик"] | None = Field(
        default=None,
        description="Фильтр по типу объекта конфигурации 1С. Если не указан, поиск выполняется по всем типам объектов"
    )
    limit: int = Field(
        default=DEFAULT_SEARCH_LIMIT,
        description=f"Максимальное количество результатов поиска (по умолчанию {DEFAULT_SEARCH_LIMIT})",
        ge=MIN_SEARCH_LIMIT,
        le=MAX_SEARCH_LIMIT
    )


def get_query_embedding(query: str) -> List[float]:
    """Получение эмбеддинга для запроса"""
    payload = json.dumps({
        "texts": [query],
        "task": "retrieval.query"
    })
    headers = {'Content-Type': 'application/json'}
    try:
        response = requests.request(
            "POST", f"{EMBEDDING_SERVICE_URL}/embed", headers=headers, data=payload, timeout=EMBEDDING_REQUEST_TIMEOUT)

        response.raise_for_status()
        data = response.json()
        return data["embeddings"][0]
    except requests.RequestException as e:
        raise Exception(f"Ошибка получения эмбеддинга: {str(e)}")


def rag_search(query: str, collection_name: str, object_type: str = None, limit: int = DEFAULT_SEARCH_LIMIT, use_multivector: bool = True) -> List[Dict[str, Any]]:
    """Выполнение RAG-поиска в документации 1С с поддержкой мультивекторного поиска"""
    try:
        # Получение эмбеддинга для запроса
        query_embedding = get_query_embedding(query)

        # Подготовка фильтра по типу объекта
        query_filter = None
        if object_type:
            query_filter = Filter(
                must=[
                    {
                        "key": "object_type",
                        "match": {
                            "value": object_type
                        }
                    }
                ]
            )

        if use_multivector:
            # Мультивекторный поиск с RRF
            search_results = qdrant_client.query_points(
                collection_name=collection_name,
                prefetch=[
                    Prefetch(
                        query=query_embedding,
                        using=OBJECT_NAME_VECTOR,
                        filter=query_filter,
                        limit=limit * PREFETCH_LIMIT_MULTIPLIER
                    ),
                    Prefetch(
                        query=query_embedding,
                        using=FRIENDLY_NAME_VECTOR,
                        filter=query_filter,
                        limit=limit * PREFETCH_LIMIT_MULTIPLIER
                    ),
                ],
                query=FusionQuery(fusion=Fusion.RRF),
                limit=limit
            )
        else:
            # Обычный поиск по одному вектору
            search_results = qdrant_client.query_points(
                collection_name=collection_name,
                query=query_embedding,
                using=FRIENDLY_NAME_VECTOR,  # Используем friendly_name как основной вектор
                query_filter=query_filter,
                limit=limit
            )

        # Форматирование результатов
        results = []
        for result in search_results.points:
            results.append({
                "score": result.score,
                "object_name": result.payload.get("object_name", ""),
                "object_type": result.payload.get("object_type", ""),
                "description": result.payload.get("doc", "")
            })

        return results
    except Exception as e:
        raise Exception(f"Ошибка поиска в документации: {str(e)}")


@mcp.tool
def search_1c_documentation(search_params: SearchRequestMCP) -> str:
    """Поиск описания объектов конфигурации 1С Предприятие 8 в документации.

    Args:
        search_params: Параметры поиска включающие запрос, тип объекта и лимит результатов
    """
    try:

        headers = get_http_headers()
        # Определяем имя коллекции по приоритету:
        # 1. Из HTTP-заголовка x-collection-name
        # 2. Значение по умолчанию из конфигурации
        collection_name = (
            headers.get("x-collection-name") or
            COLLECTION_NAME
        )

        # Проверяем, что коллекция существует
        if not qdrant_client.collection_exists(collection_name):
            return f"Ошибка: коллекция '{collection_name}' не существует в Qdrant."

        use_multivector = True
        results = rag_search(
            search_params.query,
            collection_name,
            search_params.object_type,
            search_params.limit,
            use_multivector
        )

        if not results:
            filter_text = f" по типу '{search_params.object_type}'" if search_params.object_type else ""
            search_type = "мультивекторный" if use_multivector else "обычный"
            return f"По запросу '{search_params.query}'{filter_text} ничего не найдено в документации 1С (коллекция: {collection_name}, поиск: {search_type})."

        formatted_results = []
        filter_text = f" (фильтр по типу: {search_params.object_type})" if search_params.object_type else ""
        search_type = "мультивекторный (RRF)" if use_multivector else "обычный"
        formatted_results.append(
            f"Результаты поиска по запросу: '{search_params.query}'{filter_text} (коллекция: {collection_name}, поиск: {search_type})\n")

        for i, result in enumerate(results, 1):
            formatted_results.append(
                f"\nРезультат {i} (релевантность: {result['score']:.3f})")
            formatted_results.append(f"Объект: {result['object_name']}")
            formatted_results.append(f"Тип: {result['object_type']}")
            formatted_results.append(f"Описание:")
            formatted_results.append(f"{result['description']}")
            formatted_results.append("---")

        return "\n".join(formatted_results)

    except Exception as e:
        return f"Ошибка при поиске в документации 1С: {str(e)}"


class SyntaxSearchRequestMCP(BaseModel):
    """Модель запроса для поиска в синтаксис-помощнике 1С."""
    query: str = Field(
        description="Имя объекта/функции/свойства языка 1С либо смысловой запрос (укр/рос/англ) — например 'HTTPСервис', 'створення HTTP-сервіса', 'read file'",
        min_length=1,
        max_length=500
    )
    limit: int = Field(
        default=DEFAULT_SEARCH_LIMIT,
        description=f"Максимальное количество результатов поиска (по умолчанию {DEFAULT_SEARCH_LIMIT})",
        ge=MIN_SEARCH_LIMIT,
        le=MAX_SEARCH_LIMIT
    )


def syntax_search(query: str, limit: int) -> List[Dict[str, Any]]:
    """Мультивекторный RRF-поиск в коллекции синтаксис-помощника 1С."""
    query_embedding = get_query_embedding(query)
    search_results = qdrant_client.query_points(
        collection_name=SYNTAX_COLLECTION_NAME,
        prefetch=[
            Prefetch(query=query_embedding, using=OBJECT_NAME_VECTOR,
                     limit=limit * PREFETCH_LIMIT_MULTIPLIER),
            Prefetch(query=query_embedding, using=FRIENDLY_NAME_VECTOR,
                     limit=limit * PREFETCH_LIMIT_MULTIPLIER),
        ],
        query=FusionQuery(fusion=Fusion.RRF),
        limit=limit
    )
    results = []
    for r in search_results.points:
        p = r.payload or {}
        results.append({
            "score": r.score,
            "object_name": p.get("object_name", ""),
            "title": p.get("title", ""),
            "h1": p.get("h1", ""),
            "h2": p.get("h2", ""),
            "line": p.get("line"),
            "doc": p.get("doc", ""),
        })
    return results


@mcp.tool
def search_1c_syntax(search_params: SyntaxSearchRequestMCP) -> str:
    """Поиск в синтаксис-помощнике 1С: язык, платформенные объекты (HTTPСервис, Запрос, ТекстовыйДокумент и т.п.), функции, свойства.

    Использовать когда вопрос про встроенный язык 1С и объекты платформы.
    Для объектов конкретной конфигурации (справочники, документы, регистры) — search_1c_documentation.

    Args:
        search_params: Параметры поиска (query, limit)
    """
    try:
        if not qdrant_client.collection_exists(SYNTAX_COLLECTION_NAME):
            return f"Ошибка: коллекция '{SYNTAX_COLLECTION_NAME}' не существует в Qdrant."

        results = syntax_search(search_params.query, search_params.limit)
        if not results:
            return f"По запросу '{search_params.query}' ничего не найдено в синтаксис-помощнике 1С (коллекция: {SYNTAX_COLLECTION_NAME})."

        out = [f"Результаты поиска в синтаксис-помощнике 1С по запросу: '{search_params.query}' (коллекция: {SYNTAX_COLLECTION_NAME}, поиск: мультивекторный RRF)\n"]
        for i, r in enumerate(results, 1):
            path = " › ".join(x for x in [r["h1"], r["h2"], r["title"]] if x)
            out.append(f"\nРезультат {i} (релевантность: {r['score']:.3f})")
            out.append(f"Объект: {r['object_name']}")
            out.append(f"Путь: {path}")
            if r["line"]:
                out.append(f"Источник: Full_Context_Help.md:{r['line']}")
            out.append(f"Описание:")
            out.append(r["doc"])
            out.append("---")
        return "\n".join(out)
    except Exception as e:
        return f"Ошибка при поиске в синтаксис-помощнике 1С: {str(e)}"


class PlatformSearchRequestMCP(BaseModel):
    """Модель запроса для поиска в API платформы 1С (распарсенный shcntx_*.hbk)."""
    query: str = Field(
        description=(
            "Имя элемента API: тип ('ТаблицаЗначений', 'HTTPСоединение'), "
            "метод ('НайтиПоКоду', 'СтрНайти'), свойство ('Колонки'), "
            "конструктор. Поддерживается смысловой поиск укр/рос/англ."
        ),
        min_length=1,
        max_length=500,
    )
    kind: Literal[
        "global_method", "global_property", "type",
        "type_method", "type_property", "constructor",
    ] | None = Field(
        default=None,
        description=(
            "Фильтр по виду элемента: 'type' — типы, 'type_method' — методы типа, "
            "'type_property' — свойства типа, 'constructor' — конструкторы, "
            "'global_method' / 'global_property' — глобальные методы/свойства."
        ),
    )
    parent_type: str | None = Field(
        default=None,
        description="Имя родительского типа (для type_method/type_property/constructor).",
    )
    limit: int = Field(
        default=DEFAULT_SEARCH_LIMIT,
        description=f"Максимальное количество результатов поиска (по умолчанию {DEFAULT_SEARCH_LIMIT})",
        ge=MIN_SEARCH_LIMIT,
        le=MAX_SEARCH_LIMIT,
    )


def platform_search(query: str, limit: int, kind: str | None = None,
                    parent_type: str | None = None) -> List[Dict[str, Any]]:
    """Мультивекторный RRF-поиск в коллекции 1c_platform с опциональными фильтрами."""
    query_embedding = get_query_embedding(query)
    must = []
    if kind:
        must.append({"key": "kind", "match": {"value": kind}})
    if parent_type:
        must.append({"key": "parent_type", "match": {"value": parent_type}})
    qfilter = Filter(must=must) if must else None

    search_results = qdrant_client.query_points(
        collection_name=PLATFORM_COLLECTION_NAME,
        prefetch=[
            Prefetch(query=query_embedding, using=OBJECT_NAME_VECTOR,
                     filter=qfilter, limit=limit * PREFETCH_LIMIT_MULTIPLIER),
            Prefetch(query=query_embedding, using=FRIENDLY_NAME_VECTOR,
                     filter=qfilter, limit=limit * PREFETCH_LIMIT_MULTIPLIER),
        ],
        query=FusionQuery(fusion=Fusion.RRF),
        limit=limit,
    )
    results = []
    for r in search_results.points:
        p = r.payload or {}
        results.append({
            "score": r.score,
            "object_name": p.get("object_name", ""),
            "friendly_name": p.get("friendly_name", ""),
            "kind": p.get("kind", ""),
            "parent_type": p.get("parent_type"),
            "return_type": p.get("return_type", ""),
            "doc": p.get("doc", ""),
        })
    return results


@mcp.tool
def search_1c_platform(search_params: PlatformSearchRequestMCP) -> str:
    """Поиск по API платформы 1С Предприятие 8.3 (типы, методы, свойства, конструкторы).

    Источник — распарсенный shcntx_*.hbk текущей установки платформы (актуально под версию).
    Используйте, когда нужны:
      - точные сигнатуры методов с типами возврата;
      - полный список членов типа;
      - конструкторы (Новый Тип(...));
      - актуальные данные под установленную версию платформы.

    Для разделов справочника по конфигурации (Справочники/Документы/Регистры) — search_1c_documentation.
    Для развёрнутых описаний из Full_Context_Help.md — search_1c_syntax.

    Args:
        search_params: query + опциональные фильтры (kind, parent_type) + limit.
    """
    try:
        if not qdrant_client.collection_exists(PLATFORM_COLLECTION_NAME):
            return f"Ошибка: коллекция '{PLATFORM_COLLECTION_NAME}' не существует в Qdrant."

        results = platform_search(
            search_params.query,
            search_params.limit,
            kind=search_params.kind,
            parent_type=search_params.parent_type,
        )
        if not results:
            return (
                f"По запросу '{search_params.query}' "
                f"(kind={search_params.kind}, parent_type={search_params.parent_type}) "
                f"ничего не найдено в '{PLATFORM_COLLECTION_NAME}'."
            )

        out = [
            f"Результаты поиска по API платформы 1С: '{search_params.query}' "
            f"(коллекция: {PLATFORM_COLLECTION_NAME}, мультивекторный RRF)\n"
        ]
        for i, r in enumerate(results, 1):
            out.append(f"\nРезультат {i} (релевантность: {r['score']:.3f})")
            out.append(f"Объект: {r['object_name']}")
            out.append(f"Контекст: {r['friendly_name']}")
            out.append(f"Вид: {r['kind']}")
            if r.get("parent_type"):
                out.append(f"Родительский тип: {r['parent_type']}")
            if r.get("return_type"):
                out.append(f"Тип значения: {r['return_type']}")
            out.append("Описание:")
            out.append(r["doc"])
            out.append("---")
        return "\n".join(out)
    except Exception as e:
        return f"Ошибка при поиске в API платформы 1С: {str(e)}"


class CodeSearchRequestMCP(BaseModel):
    """Модель запроса для поиска по коду конфигурации (.bsl) — коллекция 1c_acc_code."""
    query: str = Field(
        description=(
            "Имя процедуры/функции либо смысловой запрос (укр/рос/англ) — например "
            "'ОбработкаПроведения', 'где рассчитывается НДС', 'обработчик при открытии формы'"
        ),
        min_length=1,
        max_length=500,
    )
    object_type: str | None = Field(
        default=None,
        description=(
            "Фильтр по типу объекта-владельца: 'CommonModule', 'Document', 'Catalog', "
            "'Report', 'DataProcessor', 'InformationRegister', 'AccumulationRegister', "
            "'CommonForm', 'ExchangePlan' и т.п."
        ),
    )
    object_ref: str | None = Field(
        default=None,
        description="Конкретный объект-владелец в формате 'Тип.Имя', например 'Document.РеализацияТоваровУслуг'.",
    )
    routine_kind: Literal["procedure", "function"] | None = Field(
        default=None,
        description="Фильтр: 'procedure' — процедуры, 'function' — функции.",
    )
    limit: int = Field(
        default=DEFAULT_SEARCH_LIMIT,
        description=f"Максимальное количество результатов поиска (по умолчанию {DEFAULT_SEARCH_LIMIT})",
        ge=MIN_SEARCH_LIMIT,
        le=MAX_SEARCH_LIMIT,
    )


def code_search(query: str, limit: int, object_type: str | None = None,
                object_ref: str | None = None,
                routine_kind: str | None = None,
                collection: str = CODE_COLLECTION_NAME) -> List[Dict[str, Any]]:
    """Мультивекторный RRF-поиск по коду конфигурации с фильтрами.

    collection — имя коллекции Qdrant с кодом (.bsl): 1c_acc_code (Бухгалтерия)
    или 1c_utp_code (УправлениеТорговымПредприятием). Структура точек идентична.
    """
    query_embedding = get_query_embedding(query)
    must = []
    if object_type:
        must.append({"key": "object_type", "match": {"value": object_type}})
    if object_ref:
        must.append({"key": "object_ref", "match": {"value": object_ref}})
    if routine_kind:
        must.append({"key": "routine_kind", "match": {"value": routine_kind}})
    qfilter = Filter(must=must) if must else None

    search_results = qdrant_client.query_points(
        collection_name=collection,
        prefetch=[
            Prefetch(query=query_embedding, using=OBJECT_NAME_VECTOR,
                     filter=qfilter, limit=limit * PREFETCH_LIMIT_MULTIPLIER),
            Prefetch(query=query_embedding, using=FRIENDLY_NAME_VECTOR,
                     filter=qfilter, limit=limit * PREFETCH_LIMIT_MULTIPLIER),
        ],
        query=FusionQuery(fusion=Fusion.RRF),
        limit=limit,
    )
    results = []
    for r in search_results.points:
        p = r.payload or {}
        results.append({
            "score": r.score,
            "object_name": p.get("object_name", ""),
            "routine_kind": p.get("routine_kind", ""),
            "signature": p.get("signature", ""),
            "object_ref": p.get("object_ref", ""),
            "module_kind": p.get("module_kind", ""),
            "form_name": p.get("form_name", ""),
            "export": p.get("export", False),
            "directive": p.get("directive", ""),
            "file": p.get("file", ""),
            "line": p.get("line"),
            "doc": p.get("doc", ""),
        })
    return results


def run_code_search(search_params: CodeSearchRequestMCP, collection: str) -> str:
    """Общая реализация поиска по коду для коллекций 1c_acc_code / 1c_utp_code."""
    try:
        if not qdrant_client.collection_exists(collection):
            return f"Ошибка: коллекция '{collection}' не существует в Qdrant."

        results = code_search(
            search_params.query,
            search_params.limit,
            object_type=search_params.object_type,
            object_ref=search_params.object_ref,
            routine_kind=search_params.routine_kind,
            collection=collection,
        )
        if not results:
            return (
                f"По запросу '{search_params.query}' "
                f"(object_type={search_params.object_type}, object_ref={search_params.object_ref}, "
                f"routine_kind={search_params.routine_kind}) "
                f"ничего не найдено в '{collection}'."
            )

        out = [
            f"Результаты поиска по коду конфигурации: '{search_params.query}' "
            f"(коллекция: {collection}, мультивекторный RRF)\n"
        ]
        for i, r in enumerate(results, 1):
            loc = r["object_ref"]
            if r.get("form_name"):
                loc += f" / форма {r['form_name']}"
            elif r.get("module_kind") and r["module_kind"] != "module":
                loc += f" / {r['module_kind']}"
            flags = []
            if r.get("export"):
                flags.append("Экспорт")
            if r.get("directive"):
                flags.append(r["directive"])
            flag_text = f" [{', '.join(flags)}]" if flags else ""
            out.append(f"\nРезультат {i} (релевантность: {r['score']:.3f})")
            out.append(f"Процедура/функция: {r['object_name']} ({r['routine_kind']}){flag_text}")
            out.append(f"Объект: {loc}")
            if r.get("signature"):
                out.append(f"Сигнатура: {r['signature']}")
            if r.get("file"):
                src = r["file"] + (f":{r['line']}" if r.get("line") else "")
                out.append(f"Источник: {src}")
            out.append("Код:")
            out.append(r["doc"])
            out.append("---")
        return "\n".join(out)
    except Exception as e:
        return f"Ошибка при поиске по коду конфигурации 1С: {str(e)}"


@mcp.tool
def search_1c_acc_code(search_params: CodeSearchRequestMCP) -> str:
    """Поиск по коду (.bsl) конфигурации БухгалтерияДляУкраины (unpack_acc).

    Коллекция 1c_acc_code: каждая точка — одна процедура/функция из модулей объектов,
    форм, менеджеров и команд конфигурации. Используйте, когда нужно найти РЕАЛИЗАЦИЮ
    в коде этой базы:
      - конкретную процедуру/функцию по имени (ОбработкаПроведения, ПриОткрытии);
      - где что-то вычисляется/заполняется по смыслу («где рассчитывается НДС»);
      - обработчики событий объекта/формы.

    Это БУХГАЛТЕРИЯ. Для торговой конфигурации УТП — search_1c_utp_code.
    Для описания состава объекта (реквизиты/табличные части) — search_1c_documentation.
    Для языка и платформы — search_1c_syntax / search_1c_platform.

    Args:
        search_params: query + опциональные фильтры (object_type, object_ref, routine_kind) + limit.
    """
    return run_code_search(search_params, CODE_COLLECTION_NAME)


@mcp.tool
def search_1c_utp_code(search_params: CodeSearchRequestMCP) -> str:
    """Поиск по коду (.bsl) конфигурации УправлениеТорговымПредприятием (unpack_utp_comefor).

    Коллекция 1c_utp_code: каждая точка — одна процедура/функция из модулей объектов,
    форм, менеджеров и команд ТОРГОВОЙ конфигурации УТП (Управління торгівлею для
    України, ред. 2.2 / come_for). Используйте, когда нужно найти РЕАЛИЗАЦИЮ в коде
    именно этой базы:
      - конкретную процедуру/функцию по имени (ОбработкаПроведения, ПриОткрытии);
      - где что-то вычисляется/заполняется по смыслу («где формируется прайс», «ТСД ДатаМобайл»);
      - обработчики событий объекта/формы.

    Это ТОРГОВЛЯ (УТП). Для бухгалтерской конфигурации — search_1c_acc_code.
    Для описания состава объекта (реквизиты/табличные части) — search_1c_documentation.
    Для языка и платформы — search_1c_syntax / search_1c_platform.

    Args:
        search_params: query + опциональные фильтры (object_type, object_ref, routine_kind) + limit.
    """
    return run_code_search(search_params, UTP_CODE_COLLECTION_NAME)


@mcp.custom_route("/", methods=["GET"])
async def root(request: Request) -> JSONResponse:
    return JSONResponse({"message": "MCP 1C RAG Server запущен"})


@mcp.custom_route("/health", methods=["GET"])
async def health_check(request: Request) -> JSONResponse:
    """Проверка работоспособности сервера и подключений"""
    try:
        # Проверяем подключение к Qdrant
        collections = qdrant_client.get_collections()
        qdrant_status = "OK"

        # Проверяем сервис эмбеддингов
        embedding_status = "OK"
        try:
            response = requests.get(
                f"{EMBEDDING_SERVICE_URL}/health", timeout=HEALTH_CHECK_TIMEOUT)
            if response.status_code != 200:
                embedding_status = "UNAVAILABLE"
        except:
            embedding_status = "UNAVAILABLE"

        return JSONResponse({
            "status": "healthy",
            "qdrant": qdrant_status,
            "embedding_service": embedding_status,
            "collection": COLLECTION_NAME
        })
    except Exception as e:
        return JSONResponse({
            "status": "unhealthy",
            "error": str(e)
        })


@mcp.custom_route("/search", methods=["POST"])
async def manual_search(request: Request) -> JSONResponse:
    """REST endpoint для ручного тестирования поиска"""
    try:
        req_data = await request.json()

        # Валидация данных через Pydantic модель
        search_request = SearchRequest(**req_data)

        # Определяем имя коллекции по приоритету:
        # 1. Из HTTP-заголовка x-collection-name
        # 2. Значение по умолчанию из конфигурации
        collection_name = (
            request.headers.get("x-collection-name") or
            COLLECTION_NAME
        )

        # Проверяем, что коллекция существует
        if not qdrant_client.collection_exists(collection_name):
            return JSONResponse({
                "error": f"Коллекция '{collection_name}' не существует в Qdrant."
            }, status_code=400)

        # Выполнение поиска
        results = rag_search(
            query=search_request.query,
            collection_name=collection_name,
            object_type=search_request.object_type,
            limit=search_request.limit,
            use_multivector=search_request.use_multivector
        )

        return JSONResponse({
            "query": search_request.query,
            "object_type": search_request.object_type,
            "collection_name": collection_name,
            "limit": search_request.limit,
            "use_multivector": search_request.use_multivector,
            "results_count": len(results),
            "results": results
        })

    except ValueError as e:
        # Ошибки валидации Pydantic
        return JSONResponse({
            "error": f"Ошибка валидации данных: {str(e)}"
        }, status_code=400)
    except Exception as e:
        return JSONResponse({
            "error": f"Ошибка поиска: {str(e)}"
        }, status_code=500)


MCP_AUTH_TOKEN = os.getenv("MCP_AUTH_TOKEN", "").strip()
AUTH_ALLOWLIST_PATHS = ("/health", "/")


class BearerAuthMiddleware(BaseHTTPMiddleware):
    """Захищає всі маршрути крім /health і / Bearer-token-ом з MCP_AUTH_TOKEN."""

    async def dispatch(self, request: Request, call_next):
        if not MCP_AUTH_TOKEN:
            return await call_next(request)
        if request.url.path in AUTH_ALLOWLIST_PATHS:
            return await call_next(request)
        auth = request.headers.get("authorization", "")
        if auth != f"Bearer {MCP_AUTH_TOKEN}":
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        return await call_next(request)


if __name__ == "__main__":
    if MCP_AUTH_TOKEN:
        import uvicorn
        http_app = mcp.http_app(transport="streamable-http", path="/mcp")
        http_app.add_middleware(BearerAuthMiddleware)
        uvicorn.run(http_app, host=SERVER_HOST, port=SERVER_PORT, log_level="info")
    else:
        mcp.run(transport="streamable-http", host=SERVER_HOST,
                port=SERVER_PORT, log_level="info")
