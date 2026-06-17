"""
通用 Plan-Execute-Replan 服务
基于 LangGraph 官方教程实现
"""

from collections.abc import AsyncGenerator
from typing import Any

from langgraph.graph import END, StateGraph
from loguru import logger

from app.agent.aiops import OnCallState, diagnosis, executor, planner, reporter, triage
from app.agent.aiops.diagnosis import route_after_diagnosis
from app.agent.aiops.executor import route_after_executor
from app.config import config
from app.services.checkpoint_service import create_sqlite_checkpointer, setup_checkpointer
from app.services.diagnosis_memory_service import DiagnosisMemoryService, diagnosis_memory_service

# 协调图节点名称常量
NODE_TRIAGE = "triage"
NODE_PLANNER = "planner"
NODE_EXECUTOR = "executor"
NODE_DIAGNOSIS = "diagnosis"
NODE_REPORTER = "reporter"


class AIOpsService:
    """通用 Plan-Execute-Replan 服务"""

    def __init__(
        self,
        memory_service: DiagnosisMemoryService | None = None,
        checkpoint_db_path: str | None = None,
        checkpointer: Any | None = None,
    ):
        """初始化服务"""
        self.checkpoint_db_path = checkpoint_db_path or config.checkpoint_db_path
        self.checkpointer = checkpointer
        self.memory_service = memory_service or diagnosis_memory_service
        self.graph = self._build_graph() if self.checkpointer is not None else None
        logger.info("Plan-Execute-Replan Service 初始化完成")

    @staticmethod
    def _thread_id(session_id: str) -> str:
        return f"aiops:{session_id}"

    def _build_initial_state(
        self,
        *,
        user_input: str,
        session_id: str,
        case_id: str,
    ) -> OnCallState:
        """构建 OnCall 协调图的初始状态。"""

        return {
            "input": user_input,
            "session_id": session_id,
            "case_id": case_id,
            "route": "aiops",
            "route_reason": "",
            "incident": {},
            "plan": [],
            "past_steps": [],
            "evidence": [],
            "diagnosis": {},
            "response": "",
            "iteration": 0,
            "max_iterations": 2,
            "events": [],
        }

    def _build_graph(self):
        """构建 Supervisor 协调的 OnCall 多智能体工作流"""
        logger.info("构建 OnCall 协调图...")

        # 创建状态图
        workflow = StateGraph(OnCallState)

        # 添加节点：Triage -> Planner -> Executor(证据收集) -> Diagnosis -> Reporter
        workflow.add_node(NODE_TRIAGE, triage)
        workflow.add_node(NODE_PLANNER, planner)
        workflow.add_node(NODE_EXECUTOR, executor)
        workflow.add_node(NODE_DIAGNOSIS, diagnosis)
        workflow.add_node(NODE_REPORTER, reporter)

        # 设置入口点
        workflow.set_entry_point(NODE_TRIAGE)

        # 定义边
        workflow.add_edge(NODE_TRIAGE, NODE_PLANNER)
        workflow.add_edge(NODE_PLANNER, NODE_EXECUTOR)

        # 执行后的条件边：计划仍有剩余步骤则回到 Executor 逐步取证，否则进入诊断
        workflow.add_conditional_edges(
            NODE_EXECUTOR,
            route_after_executor,
            {
                NODE_EXECUTOR: NODE_EXECUTOR,
                NODE_DIAGNOSIS: NODE_DIAGNOSIS,
            },
        )

        # 诊断后的条件边：证据充分则出报告，否则回到规划继续取证（受 max_iterations 约束）
        workflow.add_conditional_edges(
            NODE_DIAGNOSIS,
            route_after_diagnosis,
            {
                NODE_PLANNER: NODE_PLANNER,
                NODE_REPORTER: NODE_REPORTER,
            },
        )
        workflow.add_edge(NODE_REPORTER, END)

        # 编译工作流
        compiled_graph = workflow.compile(checkpointer=self.checkpointer)

        logger.info("OnCall 协调图构建完成")
        return compiled_graph

    async def _initialize_graph(self) -> None:
        checkpointer = getattr(self, "checkpointer", None)
        graph = getattr(self, "graph", None)
        if checkpointer is None:
            if graph is not None:
                return
            self.checkpoint_db_path = getattr(self, "checkpoint_db_path", config.checkpoint_db_path)
            self.checkpointer = create_sqlite_checkpointer(self.checkpoint_db_path)
            checkpointer = self.checkpointer
        await setup_checkpointer(checkpointer)
        if graph is None:
            self.graph = self._build_graph()

    async def execute(
        self,
        user_input: str,
        session_id: str = "default"
    ) -> AsyncGenerator[dict[str, Any], None]:
        """
        执行 Plan-Execute-Replan 流程

        Args:
            user_input: 用户的任务描述
            session_id: 会话ID

        Yields:
            Dict[str, Any]: 流式事件
        """
        logger.info(f"[会话 {session_id}] 开始执行任务: {user_input}")
        await self._initialize_graph()

        case_id = ""
        try:
            case_id = self.memory_service.create_case(session_id=session_id, user_input=user_input)
            # 初始化状态
            initial_state = self._build_initial_state(
                user_input=user_input,
                session_id=session_id,
                case_id=case_id,
            )

            # 流式执行工作流
            config_dict = {
                "configurable": {
                    "thread_id": self._thread_id(session_id)
                },
                # Executor 自环逐步取证 + 诊断多轮，提高递归上限避免触顶（默认 25）
                "recursion_limit": 50,
            }

            if self.graph is None:
                raise RuntimeError("AIOps workflow graph is not initialized")

            # 已经透传过的规范化事件数量（每个节点返回的是累积事件列表）
            emitted_events = 0

            async for event in self.graph.astream(
                input=initial_state,
                config=config_dict,
                stream_mode="updates"
            ):
                # 解析事件
                for node_name, node_output in event.items():
                    logger.info(f"节点 '{node_name}' 输出事件")
                    self._persist_node_output(case_id, node_name, node_output)

                    # 先透传本步骤新增的规范化时间线事件
                    node_events = node_output.get("events", []) if isinstance(node_output, dict) else []
                    if len(node_events) > emitted_events:
                        for normalized_event in node_events[emitted_events:]:
                            yield normalized_event
                        emitted_events = len(node_events)

                    # 兼容旧的事件格式
                    if node_name == NODE_PLANNER:
                        yield self._format_planner_event(node_output)

                    elif node_name == NODE_EXECUTOR:
                        yield self._format_executor_event(node_output)

                    elif node_name == NODE_DIAGNOSIS:
                        yield {
                            "type": "status",
                            "stage": "diagnosis",
                            "message": "诊断判断完成",
                            "diagnosis": node_output.get("diagnosis", {}) if node_output else {},
                        }

                    elif node_name == NODE_REPORTER:
                        yield {
                            "type": "report",
                            "stage": "final_report",
                            "message": "最终报告已生成",
                            "report": node_output.get("response", "") if node_output else "",
                        }

            # 获取最终状态。AsyncSqliteSaver 必须通过 async graph API 读取检查点。
            aget_state = getattr(self.graph, "aget_state", None)
            if aget_state is not None:
                final_state = await aget_state(config_dict)
            else:
                final_state = self.graph.get_state(config_dict)
            final_response = ""

            # 安全地获取响应（处理 values 可能为 None 的情况）
            final_values = {}
            if final_state and final_state.values:
                final_values = dict(final_state.values)
                final_response = final_values.get("response", "")
            final_events = final_values.get("events", [])

            self.memory_service.complete_case(
                case_id,
                executed_steps=final_values.get("past_steps", []),
                final_report=final_response,
            )

            # 发送完成事件
            yield {
                "type": "complete",
                "stage": "complete",
                "message": "任务执行完成",
                "case_id": case_id,
                "response": final_response,
                "events": final_events,
            }

            logger.info(f"[会话 {session_id}] 任务执行完成")

        except Exception as e:
            logger.error(f"[会话 {session_id}] 任务执行失败: {e}", exc_info=True)
            if case_id:
                self.memory_service.fail_case(case_id, str(e))
            yield {
                "type": "error",
                "stage": "error",
                "message": f"任务执行出错: {str(e)}",
                "case_id": case_id,
            }

    async def diagnose(
        self,
        session_id: str = "default"
    ) -> AsyncGenerator[dict[str, Any], None]:
        """
        AIOps 诊断接口（兼容旧接口）

        Args:
            session_id: 会话ID

        Yields:
            Dict[str, Any]: 诊断过程的流式事件
        """
        # 使用固定的 AIOps 任务描述
        from textwrap import dedent
        aiops_task = dedent("""诊断当前系统是否存在告警，如果存在告警请详细分析告警原因并生成诊断报告，诊断报告输出格式要求：
                ```
                # 告警分析报告

                ---

                ## 📋 活跃告警清单

                | 告警名称 | 级别 | 目标服务 | 首次触发时间 | 最新触发时间 | 状态 |
                |---------|------|----------|-------------|-------------|------|
                | [告警1名称] | [级别] | [服务名] | [时间] | [时间] | 活跃 |
                | [告警2名称] | [级别] | [服务名] | [时间] | [时间] | 活跃 |

                ---

                ## 🔍 告警根因分析1 - [告警名称]

                ### 告警详情
                - **告警级别**: [级别]
                - **受影响服务**: [服务名]
                - **持续时间**: [X分钟]

                ### 症状描述
                [根据监控指标描述症状]

                ### 日志证据
                [引用查询到的关键日志]

                ### 根因结论
                [基于证据得出的根本原因]

                ---

                ## 🛠️ 处理方案执行1 - [告警名称]

                ### 已执行的排查步骤
                1. [步骤1]
                2. [步骤2]

                ### 处理建议
                [给出具体的处理建议]

                ### 预期效果
                [说明预期的效果]

                ---

                ## 🔍 告警根因分析2 - [告警名称]
                [如果有第2个告警，重复上述格式]

                ---

                ## 📊 结论

                ### 整体评估
                [总结所有告警的整体情况]

                ### 关键发现
                - [发现1]
                - [发现2]

                ### 后续建议
                1. [建议1]
                2. [建议2]

                ### 风险评估
                [评估当前风险等级和影响范围]
                ```

                **重要提醒**：
                - 最终输出必须是纯 Markdown 文本，不要包含 JSON 结构
                - 所有内容必须基于工具查询的真实数据，严禁编造
                - 如果某个步骤失败，在结论中如实说明，不要跳过""")

        async for event in self.execute(aiops_task, session_id):
            # 转换事件格式以兼容旧的 API
            if event.get("type") == "complete":
                # 将 response 包装为 diagnosis 格式
                yield {
                    "type": "complete",
                    "stage": "diagnosis_complete",
                    "message": "诊断流程完成",
                    "diagnosis": {
                        "status": "completed",
                        "case_id": event.get("case_id", ""),
                        "report": event.get("response", "")
                    },
                    "events": event.get("events", []),
                }
            else:
                yield event

    def _format_planner_event(self, state: dict | None) -> dict:
        """格式化 Planner 节点事件"""
        if not state:
            return {
                "type": "status",
                "stage": "planner",
                "message": "规划节点执行中"
            }

        plan = state.get("plan", [])

        return {
            "type": "plan",
            "stage": "plan_created",
            "message": f"执行计划已制定，共 {len(plan)} 个步骤",
            "plan": plan
        }

    def _format_executor_event(self, state: dict | None) -> dict:
        """格式化 Executor 节点事件"""
        if not state:
            return {
                "type": "status",
                "stage": "executor",
                "message": "执行节点运行中"
            }

        plan = state.get("plan", [])
        past_steps = state.get("past_steps", [])

        if past_steps:
            last_entry = past_steps[-1]
            if isinstance(last_entry, dict):
                last_step = last_entry.get("description") or last_entry.get("step_id") or ""
            else:
                last_step = last_entry[0] if isinstance(last_entry, (tuple, list)) else last_entry
            return {
                "type": "step_complete",
                "stage": "step_executed",
                "message": f"步骤执行完成 ({len(past_steps)}/{len(past_steps) + len(plan)})",
                "current_step": last_step,
                "remaining_steps": len(plan)
            }
        else:
            return {
                "type": "status",
                "stage": "executor",
                "message": "开始执行步骤"
            }

    def _persist_node_output(self, case_id: str, node_name: str, state: dict | None) -> None:
        if node_name != NODE_PLANNER or not state:
            return

        plan = state.get("plan")
        if isinstance(plan, list):
            self.memory_service.update_case_plan(case_id, plan)


# 全局单例
aiops_service = AIOpsService()
