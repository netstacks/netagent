"""Workflow execution task."""

import logging
from datetime import datetime
from celery import shared_task

from netagent_core.db import get_db_context, WorkflowRun, WorkflowNodeExecution, Workflow

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3)
def execute_workflow(self, run_id: int):
    """Execute a workflow run.

    This task processes the workflow definition and executes each node
    in order, handling conditions, parallel execution, and agent handoffs.
    """
    logger.info(f"Starting workflow execution: run_id={run_id}")

    with get_db_context() as db:
        # Get workflow run
        run = db.query(WorkflowRun).filter(WorkflowRun.id == run_id).first()
        if not run:
            logger.error(f"Workflow run not found: {run_id}")
            return {"error": "Workflow run not found"}

        # Get workflow definition
        workflow = db.query(Workflow).filter(Workflow.id == run.workflow_id).first()
        if not workflow:
            logger.error(f"Workflow not found: {run.workflow_id}")
            run.status = "failed"
            run.error_message = "Workflow definition not found"
            db.commit()
            return {"error": "Workflow not found"}

        # Update status to running
        run.status = "running"
        run.started_at = datetime.utcnow()
        db.commit()

        try:
            # Parse workflow definition
            definition = workflow.definition
            nodes = {n["id"]: n for n in definition.get("nodes", [])}
            edges = definition.get("edges", [])

            # Find start node
            start_node = next(
                (n for n in nodes.values() if n["type"] == "start"),
                None
            )

            if not start_node:
                raise ValueError("Workflow has no start node")

            # Execute workflow
            context = run.context or {}
            context["trigger_data"] = run.trigger_data or {}

            current_node_id = start_node["id"]

            while current_node_id:
                node = nodes.get(current_node_id)
                if not node:
                    break

                run.current_node_id = current_node_id
                db.commit()

                # Execute node
                result = execute_node(db, run, node, context, edges)

                # Update context with result
                if result.get("output"):
                    context[f"node_{current_node_id}"] = result["output"]

                # Determine next node
                if result.get("status") == "waiting_approval":
                    run.status = "waiting_approval"
                    db.commit()
                    return {"status": "waiting_approval", "node_id": current_node_id}

                next_node_id = result.get("next_node_id")
                current_node_id = next_node_id

            # Workflow completed
            run.status = "completed"
            run.completed_at = datetime.utcnow()
            run.context = context
            db.commit()

            logger.info(f"Workflow completed: run_id={run_id}")
            return {"status": "completed", "run_id": run_id}

        except Exception as e:
            logger.exception(f"Workflow execution failed: {e}")
            run.status = "failed"
            run.completed_at = datetime.utcnow()
            run.error_message = str(e)
            db.commit()
            return {"error": str(e)}


def execute_node(db, run, node, context, edges):
    """Execute a single workflow node.

    Returns:
        dict with status, output, and next_node_id
    """
    node_id = node["id"]
    node_type = node["type"]
    node_config = node.get("config", {})

    logger.info(f"Executing node: {node_id} ({node_type})")

    # Create node execution record
    execution = WorkflowNodeExecution(
        workflow_run_id=run.id,
        node_id=node_id,
        node_type=node_type,
        status="running",
        input_data={"context": context, "config": node_config},
        started_at=datetime.utcnow(),
    )
    db.add(execution)
    db.commit()

    try:
        result = {}

        if node_type == "start":
            # Start node just passes through
            result = {"output": context.get("trigger_data", {})}

        elif node_type == "agent":
            # Execute agent
            result = execute_agent_node(db, run, node_config, context)

        elif node_type == "condition":
            # Evaluate condition
            result = evaluate_condition_node(node_config, context)

        elif node_type in ["output_email", "output_slack", "output_jira"]:
            # Execute output node
            result = execute_output_node(node_type, node_config, context)

        elif node_type == "parallel":
            # TODO: Implement parallel execution
            result = {"output": {"message": "Parallel not yet implemented"}}

        elif node_type == "join":
            # TODO: Implement join
            result = {"output": {"message": "Join not yet implemented"}}

        else:
            result = {"output": {"message": f"Unknown node type: {node_type}"}}

        # Find next node based on output port
        output_port = result.get("output_port", "success")
        next_node_id = find_next_node(node_id, output_port, edges)

        # Update execution record
        execution.status = result.get("status", "completed")
        execution.output_data = result.get("output")
        execution.completed_at = datetime.utcnow()
        db.commit()

        return {
            "status": result.get("status", "completed"),
            "output": result.get("output"),
            "next_node_id": next_node_id,
        }

    except Exception as e:
        execution.status = "failed"
        execution.error_message = str(e)
        execution.completed_at = datetime.utcnow()
        db.commit()
        raise


def execute_agent_node(db, run, config, context):
    """Execute an agent node."""
    from netagent_core.db import Agent, AgentSession

    agent_id = config.get("agent_id")
    if not agent_id:
        return {"output": {"error": "No agent_id configured"}, "status": "failed"}

    agent = db.query(Agent).filter(Agent.id == agent_id).first()
    if not agent:
        return {"output": {"error": f"Agent {agent_id} not found"}, "status": "failed"}

    # Create agent session
    session = AgentSession(
        agent_id=agent_id,
        workflow_run_id=run.id,
        status="active",
        trigger_type="workflow",
        context=context,
    )
    db.add(session)
    db.commit()

    # TODO: Execute agent with context
    # This would call the agent executor to run the ReAct loop

    # For now, return placeholder
    return {
        "output": {
            "session_id": session.id,
            "message": "Agent execution placeholder",
        },
        "status": "completed",
    }


def evaluate_condition_node(config, context):
    """Evaluate a condition node."""
    field = config.get("field", "")
    operator = config.get("operator", "equals")
    value = config.get("value", "")

    # Get field value from context (supports dot notation)
    actual_value = context
    for part in field.split("."):
        if isinstance(actual_value, dict):
            actual_value = actual_value.get(part)
        else:
            actual_value = None
            break

    # Evaluate condition
    result = False
    if operator == "equals":
        result = actual_value == value
    elif operator == "not_equals":
        result = actual_value != value
    elif operator == "contains":
        result = value in str(actual_value) if actual_value else False
    elif operator == "gt":
        result = float(actual_value or 0) > float(value)
    elif operator == "lt":
        result = float(actual_value or 0) < float(value)
    elif operator == "exists":
        result = actual_value is not None

    return {
        "output": {"condition_result": result, "field": field, "value": actual_value},
        "output_port": "true" if result else "false",
    }


def execute_output_node(node_type, config, context):
    """Execute an output node (email, slack, jira)."""
    # TODO: Implement actual output sending
    # For now, just log and return success

    output_type = node_type.replace("output_", "")
    logger.info(f"Output node: {output_type} - {config}")

    return {
        "output": {
            "type": output_type,
            "config": config,
            "message": f"{output_type} output placeholder",
        },
        "status": "completed",
    }


def find_next_node(current_node_id, output_port, edges):
    """Find the next node based on edge connections."""
    for edge in edges:
        if edge["from"] == current_node_id and edge.get("fromPort", "success") == output_port:
            return edge["to"]
    return None
