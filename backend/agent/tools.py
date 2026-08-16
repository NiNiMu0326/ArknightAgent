"""
Tool definitions and execution for AgenticRAG.
Defines tool schemas and dispatches tool calls to implementations.
"""

import logging
from typing import Dict, List, Any, Optional, Callable

logger = logging.getLogger(__name__)


# ===== Tool Schemas (OpenAI Function Calling format) =====

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "arknights_rag_search",
            "description": "在明日方舟知识库中检索相关文档内容。适用于查询干员技能、属性、剧情、关卡攻略等知识库中存在的内容。返回文档片段及相关性分数。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索关键词或问题"
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "返回结果数量，默认5"
                    },
                    "search_mode": {
                        "type": "string",
                        "enum": ["precise", "semantic", "balanced"],
                        "description": "检索模式：precise=关键词精确匹配(数值/属性/技能名查询)；semantic=语义理解(剧情/关系/设定查询)；balanced=均衡检索(默认)"
                    }
                },
                "required": ["query"],
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "arknights_graphrag_search",
            "description": "查询明日方舟实体（干员、组织、地点、事件）之间的关系。支持两种模式：\n1. 传入 entity（一个实体）：返回该实体的所有直接邻居及关系\n2. 传入 entity1 + entity2（两个实体）：返回两实体间的最短关系路径，路径结果包含每个中间节点名称、每条边的关系类型和详细描述，通常无需再单独查询路径上的实体\n\n适用于需要了解角色血缘、所属组织、战友关系，或发现间接关系。路径查询结果已包含完整的边关系信息，无需对路径上的实体重复调用此工具。",
            "parameters": {
                "type": "object",
                "properties": {
                    "entity": {
                        "type": "string",
                        "description": "要查询的实体名称（单实体模式）"
                    },
                    "entity1": {
                        "type": "string",
                        "description": "起始实体名称（双实体路径模式）"
                    },
                    "entity2": {
                        "type": "string",
                        "description": "目标实体名称（双实体路径模式）"
                    }
                },
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "在互联网上搜索信息。当知识库中没有足够信息，或需要最新资讯时使用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索关键词"
                    }
                },
                "required": ["query"],
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "arknights_stage_waves",
            "description": "查询指定关卡的出怪顺序（波次与刷怪顺序）。返回每波敌人出现的先后顺序、敌人名称、数量、间隔等。参数 stage_code 传关卡编号（如 1-7、CE-5、WD-EX-8）或关卡 ID（如 main_01-07）。注意：只查敌人种类与总数时用 get_stage_enemies，查先后顺序时用本工具。",
            "parameters": {
                "type": "object",
                "properties": {
                    "stage_code": {
                        "type": "string",
                        "description": "关卡编号或关卡 ID，如 '1-7'、'CE-5'、'WD-EX-8'、'main_01-07'"
                    }
                },
                "required": ["stage_code"],
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "arknights_structured_query",
            "description": "使用 SQL 精确查询干员/敌人的结构化数值数据。适用于需要数值比较（攻击力>700）、排序（按防御排序）、统计（计数、平均值）等精确查询。表结构：operators(干员: name/rarity/class/branch/hp_elite2/atk_elite2/def_elite2/mres_elite2/...), enemies(敌人: name/category/rank/hp/atk/def/mres/...)。字符串值用单引号括起来。",
            "parameters": {
                "type": "object",
                "properties": {
                    "sql": {
                        "type": "string",
                        "description": "SQL SELECT 查询语句。表名: operators（干员）, enemies（敌人）。operators 表关键列: name(干员名), rarity(星级1-6), class(职业), branch(分支), hp_elite2(生命), atk_elite2(攻击), def_elite2(防御), mres_elite2(法抗)。enemies 表关键列: name(名称), rank(地位级别:普通/精英/领袖), category(种类), hp(生命), atk(攻击), def(防御), mres(法抗)。例: SELECT name, rarity, atk_elite2 FROM operators WHERE class='近卫' AND atk_elite2 > 700 ORDER BY atk_elite2 DESC LIMIT 10"
                    }
                },
                "required": ["sql"],
                "additionalProperties": False
            }
        }
    }
]


# ===== Tool Registry =====

class ToolRegistry:
    """Registry that maps tool names to their executor functions."""

    def __init__(self):
        self._tools: Dict[str, Callable] = {}
        self._dynamic_schemas: List[Dict] = []

    def register(self, name: str, executor: Callable):
        """Register a tool executor function."""
        self._tools[name] = executor
        logger.info(f"Registered tool: {name}")

    def register_schema(self, schema: Dict[str, Any]) -> None:
        """Register an OpenAI Function Calling schema (idempotent by tool name).

        The replacement list is built and assigned in a single step so readers
        can never observe a half-registered state.
        """
        if not isinstance(schema, dict) or not isinstance(schema.get("function"), dict):
            raise TypeError("register_schema 需要 {'type': 'function', 'function': {...}} 结构")
        name = schema["function"].get("name", "")
        if not isinstance(name, str) or not name:
            raise ValueError("tool schema 的 function.name 必须是非空字符串")
        self._dynamic_schemas = [
            s for s in self._dynamic_schemas
            if (s.get("function") or {}).get("name") != name
        ] + [schema]
        logger.info(f"Registered tool schema: {name}")

    def get_schemas(self) -> List[Dict]:
        """Get all tool schemas for API calls (static + dynamically registered)."""
        return [*TOOL_SCHEMAS, *self._dynamic_schemas]

    async def execute(self, tool_name: str, arguments: Dict[str, Any], session_id: str = "") -> Any:
        """Execute a tool by name with the given arguments."""
        executor = self._tools.get(tool_name)
        if executor is None:
            raise ValueError(f"Unknown tool: {tool_name}")

        return await executor(arguments, session_id=session_id)


# ===== Singleton Registry =====

_registry: Optional[ToolRegistry] = None


def get_tool_registry() -> ToolRegistry:
    """Get or create the global tool registry."""
    global _registry
    if _registry is None:
        _registry = ToolRegistry()
        _register_default_tools(_registry)
    return _registry


def _register_default_tools(registry: ToolRegistry):
    """Register the default RAG tools."""
    from backend.agent.tool_implementations import (
        execute_rag_search,
        execute_graphrag_search,
        execute_web_search,
    )
    from backend.agent.structured_query import execute_structured_query
    from backend.agent.stage_waves import execute_stage_waves
    registry.register("arknights_rag_search", execute_rag_search)
    registry.register("arknights_graphrag_search", execute_graphrag_search)
    registry.register("web_search", execute_web_search)
    registry.register("arknights_structured_query", execute_structured_query)
    registry.register("arknights_stage_waves", execute_stage_waves)
