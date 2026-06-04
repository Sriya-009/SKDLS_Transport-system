"""AI Workflow Engine for multi-step, persistent workflows.

Provides sequential step execution with persistence, retries, rollback and socket events.
"""
from __future__ import annotations

import time
import uuid
from typing import Any, Callable, Dict, List, Optional


class WorkflowEngine:
    def __init__(self, *, workflow_service: Any, tool_handlers: Dict[str, Callable[..., Any]], emit_event: Callable[[str, dict], None], logger: Any, db_save_memory: Callable[..., Any], db_save_action: Callable[..., Any], db_save_metric: Callable[..., Any]):
        self.workflow_service = workflow_service
        self.tool_handlers = dict(tool_handlers or {})
        self.emit_event = emit_event
        self.logger = logger
        self.save_workflow_memory = db_save_memory
        self.save_ai_action_log = db_save_action
        self.save_ai_tool_metric = db_save_metric

    def _now_ms(self):
        return int(time.time() * 1000)

    def _emit(self, name: str, payload: dict):
        try:
            self.emit_event(name, payload)
        except Exception:
            try:
                self.logger.exception(f"[workflow][emit][error] {name}")
            except Exception:
                pass

    def _persist_state(self, user_id: Optional[str], workflow_type: str, state: List[dict], current_step: str, context_json: dict):
        try:
            self.save_workflow_memory(user_id=user_id, workflow_type=workflow_type, workflow_state=state, current_step=current_step, context_json=context_json)
        except Exception:
            try:
                self.logger.exception("[workflow][persistence] failed to save workflow memory")
            except Exception:
                pass

    def _log_action(self, user_id: Optional[str], role: str, action: str, intent: str, tool_name: str, shipment_id: Optional[int], status: str, request_payload: dict, response_payload: dict, started_at: Any, completed_at: Any, duration_ms: int, error_message: Optional[str]):
        try:
            self.save_ai_action_log(
                user_id=user_id,
                role=role,
                action=action,
                intent=intent,
                tool_name=tool_name,
                shipment_id=shipment_id,
                status=status,
                request_payload=request_payload,
                response_payload=response_payload,
                error_message=error_message,
                started_at=started_at,
                completed_at=completed_at,
                duration_ms=duration_ms,
                retry_count=0,
                execution_status=status,
            )
        except Exception:
            try:
                self.logger.exception("[workflow][action_log] failed to persist")
            except Exception:
                pass

    def execute_workflow(self, *, workflow_name: str, steps: List[Dict[str, Any]], initial_context: Dict[str, Any], user_id: Optional[str] = None, role: str = 'system', max_retries: int = 2):
        execution_id = uuid.uuid4().hex
        workflow_state: List[dict] = []
        context = dict(initial_context or {})
        current_step_name = ''

        self._emit('ai:workflow:start', {'execution_id': execution_id, 'workflow': workflow_name, 'user_id': user_id, 'status': 'started'})

        for idx, step in enumerate(steps):
            step_name = str(step.get('name') or step.get('step') or f"step_{idx}")
            tool = str(step.get('tool') or '')
            retry = int(step.get('retries') or 0)
            compensation = step.get('compensation')
            payload = dict(step.get('payload') or {})
            payload.update(context or {})
            current_step_name = step_name

            attempt = 0
            success = False
            last_error = None
            started_at = time.time()
            while attempt <= max_retries:
                attempt += 1
                t0 = time.time()
                try:
                    # choose handler: tool_handlers override workflow_service
                    handler = self.tool_handlers.get(tool)
                    if handler is None:
                        # fallback to workflow service call
                        result = getattr(self.workflow_service, tool)(**payload) if hasattr(self.workflow_service, tool) else None
                    else:
                        result = handler(payload)

                    duration_ms = int((time.time() - t0) * 1000)
                    # record success action
                    self._log_action(user_id=user_id, role=role, action=step_name, intent=workflow_name, tool_name=tool or step_name, shipment_id=payload.get('shipment_id'), status='success', request_payload=payload, response_payload=result or {}, started_at=time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(started_at)), completed_at=time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime()), duration_ms=duration_ms, error_message=None)

                    workflow_state.append({'step': step_name, 'tool': tool, 'status': 'completed', 'result': result})
                    # persist memory snapshot
                    self._persist_state(user_id, workflow_name, workflow_state, current_step_name, context)
                    self._emit('ai:workflow:step', {'execution_id': execution_id, 'step': step_name, 'status': 'completed', 'result': result})
                    # merge lightweight result into context for downstream steps
                    if isinstance(result, dict):
                        context.update(result or {})
                    success = True
                    break
                except Exception as exc:
                    last_error = str(exc)
                    duration_ms = int((time.time() - t0) * 1000)
                    self._log_action(user_id=user_id, role=role, action=step_name, intent=workflow_name, tool_name=tool or step_name, shipment_id=payload.get('shipment_id'), status='error', request_payload=payload, response_payload={}, started_at=time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(started_at)), completed_at=time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime()), duration_ms=duration_ms, error_message=last_error)
                    self._emit('ai:workflow:step', {'execution_id': execution_id, 'step': step_name, 'status': 'error', 'error': last_error, 'attempt': attempt})
                    # persist failed step
                    workflow_state.append({'step': step_name, 'tool': tool, 'status': 'error', 'error': last_error, 'attempt': attempt})
                    self._persist_state(user_id, workflow_name, workflow_state, current_step_name, context)
                    if attempt > max_retries:
                        break
                    time.sleep(0.5 * attempt)

            if not success:
                # run compensation for previous steps if defined
                if compensation:
                    try:
                        comp_handler = self.tool_handlers.get(compensation) or getattr(self.workflow_service, compensation, None)
                        if comp_handler:
                            comp_handler(payload)
                            self._emit('ai:workflow:compensation', {'execution_id': execution_id, 'step': step_name, 'compensation': compensation, 'status': 'executed'})
                    except Exception:
                        self._emit('ai:workflow:compensation', {'execution_id': execution_id, 'step': step_name, 'compensation': compensation, 'status': 'failed'})

                self._emit('ai:workflow:failed', {'execution_id': execution_id, 'workflow': workflow_name, 'step': step_name, 'error': last_error})
                return {'execution_id': execution_id, 'status': 'failed', 'step': step_name, 'error': last_error, 'state': workflow_state}

        # completed all steps
        self._emit('ai:workflow:complete', {'execution_id': execution_id, 'workflow': workflow_name, 'status': 'completed', 'state': workflow_state})
        return {'execution_id': execution_id, 'status': 'completed', 'state': workflow_state, 'context': context}
