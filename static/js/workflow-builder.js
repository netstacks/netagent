/**
 * NetAgent Visual Workflow Builder
 * Canvas-based workflow editor for creating multi-agent workflows
 */

class WorkflowBuilder {
    constructor() {
        this.canvas = document.getElementById('workflow-canvas');
        this.container = document.getElementById('canvas-container');
        this.svg = document.getElementById('connections-svg');
        this.propertiesPanel = document.getElementById('properties-body');

        this.nodes = new Map();
        this.edges = [];
        this.selectedNode = null;
        this.draggingNode = null;
        this.connecting = null;
        this.zoom = 1;
        this.offset = { x: 0, y: 0 };

        this.nodeIdCounter = 1;

        this.nodeTypes = {
            start: {
                icon: 'bi-play-circle',
                color: 'secondary',
                inputs: [],
                outputs: ['trigger'],
                label: 'Start'
            },
            agent: {
                icon: 'bi-robot',
                color: 'primary',
                inputs: ['trigger'],
                outputs: ['success', 'failure'],
                label: 'Agent'
            },
            condition: {
                icon: 'bi-signpost-split',
                color: 'warning',
                inputs: ['input'],
                outputs: ['true', 'false'],
                label: 'Condition'
            },
            parallel: {
                icon: 'bi-arrows-expand',
                color: 'info',
                inputs: ['input'],
                outputs: ['branch_1', 'branch_2'],
                label: 'Parallel'
            },
            join: {
                icon: 'bi-arrows-collapse',
                color: 'info',
                inputs: ['branch_1', 'branch_2'],
                outputs: ['output'],
                label: 'Join'
            },
            output_email: {
                icon: 'bi-envelope',
                color: 'success',
                inputs: ['input'],
                outputs: [],
                label: 'Email'
            },
            output_slack: {
                icon: 'bi-slack',
                color: 'success',
                inputs: ['input'],
                outputs: [],
                label: 'Slack'
            },
            output_jira: {
                icon: 'bi-ticket',
                color: 'success',
                inputs: ['input'],
                outputs: [],
                label: 'Jira'
            }
        };

        this.init();
    }

    init() {
        // Initialize start node
        this.initStartNode();

        // Set up event listeners
        this.initCanvasEvents();
        this.initPaletteDrag();
        this.initKeyboardShortcuts();

        // Initial render
        this.render();
    }

    initStartNode() {
        const startNode = document.getElementById('node-start');
        if (startNode) {
            this.nodes.set('start', {
                id: 'start',
                type: 'start',
                position: { x: 50, y: 200 },
                config: {}
            });

            this.setupNodeEvents(startNode);
        }
    }

    initCanvasEvents() {
        // Pan canvas
        let isPanning = false;
        let panStart = { x: 0, y: 0 };

        this.container.addEventListener('mousedown', (e) => {
            if (e.target === this.container || e.target === this.canvas) {
                isPanning = true;
                panStart = { x: e.clientX - this.offset.x, y: e.clientY - this.offset.y };
                this.container.style.cursor = 'grabbing';
            }
        });

        window.addEventListener('mousemove', (e) => {
            if (isPanning) {
                this.offset.x = e.clientX - panStart.x;
                this.offset.y = e.clientY - panStart.y;
                this.applyTransform();
            }

            if (this.draggingNode) {
                this.handleNodeDrag(e);
            }

            if (this.connecting) {
                this.updateTempConnection(e);
            }
        });

        window.addEventListener('mouseup', () => {
            isPanning = false;
            this.container.style.cursor = '';

            if (this.draggingNode) {
                this.draggingNode = null;
            }

            if (this.connecting) {
                this.cancelConnection();
            }
        });

        // Click to deselect
        this.canvas.addEventListener('click', (e) => {
            if (e.target === this.canvas) {
                this.selectNode(null);
            }
        });
    }

    initPaletteDrag() {
        document.querySelectorAll('.palette-item').forEach(item => {
            item.addEventListener('dragstart', (e) => {
                e.dataTransfer.setData('node-type', item.dataset.type);
                if (item.dataset.agentId) {
                    e.dataTransfer.setData('agent-id', item.dataset.agentId);
                }
            });
        });

        this.canvas.addEventListener('dragover', (e) => {
            e.preventDefault();
        });

        this.canvas.addEventListener('drop', (e) => {
            e.preventDefault();
            const nodeType = e.dataTransfer.getData('node-type');
            const agentId = e.dataTransfer.getData('agent-id');

            if (nodeType) {
                const rect = this.canvas.getBoundingClientRect();
                const x = (e.clientX - rect.left - this.offset.x) / this.zoom;
                const y = (e.clientY - rect.top - this.offset.y) / this.zoom;

                const config = {};
                if (agentId) {
                    config.agent_id = parseInt(agentId);
                }

                this.addNode(nodeType, { x, y }, config);
            }
        });
    }

    initKeyboardShortcuts() {
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Delete' || e.key === 'Backspace') {
                if (this.selectedNode && this.selectedNode !== 'start') {
                    // Don't delete if typing in an input
                    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') {
                        return;
                    }
                    this.deleteNode(this.selectedNode);
                }
            }

            if (e.key === 'Escape') {
                this.selectNode(null);
                this.cancelConnection();
            }
        });
    }

    addNode(type, position, config = {}) {
        const id = `${type}_${this.nodeIdCounter++}`;
        const typeConfig = this.nodeTypes[type];

        if (!typeConfig) {
            console.error('Unknown node type:', type);
            return;
        }

        const node = {
            id,
            type,
            position,
            config
        };

        this.nodes.set(id, node);
        this.renderNode(node);
        this.selectNode(id);

        return id;
    }

    renderNode(node) {
        const typeConfig = this.nodeTypes[node.type];
        const element = document.createElement('div');
        element.className = `workflow-node ${node.type}-node`;
        element.id = `node-${node.id}`;
        element.style.left = `${node.position.x}px`;
        element.style.top = `${node.position.y}px`;

        // Build ports HTML
        let inputPorts = '';
        let outputPorts = '';

        typeConfig.inputs.forEach(port => {
            inputPorts += `<div class="node-port input-port" data-port="${port}" title="${port}"></div>`;
        });

        typeConfig.outputs.forEach(port => {
            outputPorts += `<div class="node-port output-port" data-port="${port}" title="${port}"></div>`;
        });

        element.innerHTML = `
            <div class="node-header bg-${typeConfig.color}">
                <i class="bi ${typeConfig.icon}"></i>
                <span class="node-title">${this.getNodeLabel(node)}</span>
                <button class="node-delete btn btn-link btn-sm text-white p-0">
                    <i class="bi bi-x"></i>
                </button>
            </div>
            <div class="node-body">
                <small class="text-muted">${typeConfig.label}</small>
            </div>
            <div class="node-ports">
                <div class="input-ports">${inputPorts}</div>
                <div class="output-ports">${outputPorts}</div>
            </div>
        `;

        this.canvas.appendChild(element);
        this.setupNodeEvents(element);
    }

    getNodeLabel(node) {
        if (node.config && node.config.agent_name) {
            return node.config.agent_name;
        }
        if (node.type === 'start') {
            return 'Start';
        }
        return node.id;
    }

    setupNodeEvents(element) {
        const nodeId = element.id.replace('node-', '');

        // Select on click
        element.addEventListener('click', (e) => {
            e.stopPropagation();
            this.selectNode(nodeId);
        });

        // Drag to move
        element.addEventListener('mousedown', (e) => {
            if (e.target.classList.contains('node-port') ||
                e.target.classList.contains('node-delete') ||
                e.target.closest('.node-delete')) {
                return;
            }

            this.draggingNode = {
                id: nodeId,
                startX: e.clientX,
                startY: e.clientY,
                nodeStartX: parseInt(element.style.left),
                nodeStartY: parseInt(element.style.top)
            };
        });

        // Delete button
        const deleteBtn = element.querySelector('.node-delete');
        if (deleteBtn && nodeId !== 'start') {
            deleteBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                this.deleteNode(nodeId);
            });
        }

        // Port connections
        element.querySelectorAll('.output-port').forEach(port => {
            port.addEventListener('mousedown', (e) => {
                e.stopPropagation();
                this.startConnection(nodeId, port.dataset.port, e);
            });
        });

        element.querySelectorAll('.input-port').forEach(port => {
            port.addEventListener('mouseup', (e) => {
                if (this.connecting) {
                    this.finishConnection(nodeId, port.dataset.port);
                }
            });

            port.addEventListener('mouseenter', () => {
                if (this.connecting) {
                    port.classList.add('port-hover');
                }
            });

            port.addEventListener('mouseleave', () => {
                port.classList.remove('port-hover');
            });
        });
    }

    handleNodeDrag(e) {
        if (!this.draggingNode) return;

        const node = this.nodes.get(this.draggingNode.id);
        if (!node) return;

        const dx = (e.clientX - this.draggingNode.startX) / this.zoom;
        const dy = (e.clientY - this.draggingNode.startY) / this.zoom;

        node.position.x = this.draggingNode.nodeStartX + dx;
        node.position.y = this.draggingNode.nodeStartY + dy;

        const element = document.getElementById(`node-${node.id}`);
        if (element) {
            element.style.left = `${node.position.x}px`;
            element.style.top = `${node.position.y}px`;
        }

        this.renderConnections();
    }

    startConnection(fromNode, fromPort, event) {
        this.connecting = {
            fromNode,
            fromPort,
            startX: event.clientX,
            startY: event.clientY
        };

        // Add temporary line
        this.renderTempConnection(event);
    }

    renderTempConnection(event) {
        const tempLine = document.getElementById('temp-connection');
        if (tempLine) tempLine.remove();

        const fromElement = document.getElementById(`node-${this.connecting.fromNode}`);
        const fromPort = fromElement.querySelector(`.output-port[data-port="${this.connecting.fromPort}"]`);

        if (!fromPort) return;

        const rect = this.canvas.getBoundingClientRect();
        const fromRect = fromPort.getBoundingClientRect();

        const x1 = (fromRect.left + fromRect.width / 2 - rect.left - this.offset.x) / this.zoom;
        const y1 = (fromRect.top + fromRect.height / 2 - rect.top - this.offset.y) / this.zoom;
        const x2 = (event.clientX - rect.left - this.offset.x) / this.zoom;
        const y2 = (event.clientY - rect.top - this.offset.y) / this.zoom;

        const line = document.createElementNS('http://www.w3.org/2000/svg', 'path');
        line.id = 'temp-connection';
        line.setAttribute('d', this.createCurvePath(x1, y1, x2, y2));
        line.setAttribute('class', 'connection-line temp');
        this.svg.appendChild(line);
    }

    updateTempConnection(event) {
        if (!this.connecting) return;
        this.renderTempConnection(event);
    }

    finishConnection(toNode, toPort) {
        if (!this.connecting) return;

        // Prevent self-connection
        if (this.connecting.fromNode === toNode) {
            this.cancelConnection();
            return;
        }

        // Check if connection already exists
        const exists = this.edges.some(e =>
            e.from === this.connecting.fromNode &&
            e.fromPort === this.connecting.fromPort &&
            e.to === toNode &&
            e.toPort === toPort
        );

        if (!exists) {
            this.edges.push({
                from: this.connecting.fromNode,
                fromPort: this.connecting.fromPort,
                to: toNode,
                toPort: toPort
            });
        }

        this.cancelConnection();
        this.renderConnections();
    }

    cancelConnection() {
        this.connecting = null;
        const tempLine = document.getElementById('temp-connection');
        if (tempLine) tempLine.remove();
    }

    renderConnections() {
        // Clear existing connections
        this.svg.innerHTML = '';

        this.edges.forEach((edge, index) => {
            const fromElement = document.getElementById(`node-${edge.from}`);
            const toElement = document.getElementById(`node-${edge.to}`);

            if (!fromElement || !toElement) return;

            const fromPort = fromElement.querySelector(`.output-port[data-port="${edge.fromPort}"]`);
            const toPort = toElement.querySelector(`.input-port[data-port="${edge.toPort}"]`);

            if (!fromPort || !toPort) return;

            const rect = this.canvas.getBoundingClientRect();
            const fromRect = fromPort.getBoundingClientRect();
            const toRect = toPort.getBoundingClientRect();

            const x1 = (fromRect.left + fromRect.width / 2 - rect.left - this.offset.x) / this.zoom;
            const y1 = (fromRect.top + fromRect.height / 2 - rect.top - this.offset.y) / this.zoom;
            const x2 = (toRect.left + toRect.width / 2 - rect.left - this.offset.x) / this.zoom;
            const y2 = (toRect.top + toRect.height / 2 - rect.top - this.offset.y) / this.zoom;

            const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
            path.setAttribute('d', this.createCurvePath(x1, y1, x2, y2));
            path.setAttribute('class', 'connection-line');
            path.dataset.edgeIndex = index;

            // Click to delete
            path.addEventListener('click', () => {
                this.edges.splice(index, 1);
                this.renderConnections();
            });

            this.svg.appendChild(path);
        });
    }

    createCurvePath(x1, y1, x2, y2) {
        const midX = (x1 + x2) / 2;
        const controlOffset = Math.abs(x2 - x1) * 0.5;

        return `M ${x1} ${y1} C ${x1 + controlOffset} ${y1}, ${x2 - controlOffset} ${y2}, ${x2} ${y2}`;
    }

    selectNode(nodeId) {
        // Deselect previous
        if (this.selectedNode) {
            const prevElement = document.getElementById(`node-${this.selectedNode}`);
            if (prevElement) {
                prevElement.classList.remove('selected');
            }
        }

        this.selectedNode = nodeId;

        if (nodeId) {
            const element = document.getElementById(`node-${nodeId}`);
            if (element) {
                element.classList.add('selected');
            }
            this.showNodeProperties(nodeId);
        } else {
            this.hideNodeProperties();
        }
    }

    deleteNode(nodeId) {
        if (nodeId === 'start') return;

        // Remove element
        const element = document.getElementById(`node-${nodeId}`);
        if (element) {
            element.remove();
        }

        // Remove from nodes
        this.nodes.delete(nodeId);

        // Remove connected edges
        this.edges = this.edges.filter(e => e.from !== nodeId && e.to !== nodeId);

        // Deselect
        if (this.selectedNode === nodeId) {
            this.selectNode(null);
        }

        this.renderConnections();
    }

    showNodeProperties(nodeId) {
        const node = this.nodes.get(nodeId);
        if (!node) return;

        const typeConfig = this.nodeTypes[node.type];

        let propertiesHtml = `
            <div class="mb-3">
                <label class="form-label small">Node ID</label>
                <input type="text" class="form-control form-control-sm" value="${node.id}" disabled>
            </div>
            <div class="mb-3">
                <label class="form-label small">Type</label>
                <input type="text" class="form-control form-control-sm" value="${typeConfig.label}" disabled>
            </div>
        `;

        // Type-specific properties
        switch (node.type) {
            case 'agent':
                propertiesHtml += this.getAgentProperties(node);
                break;
            case 'condition':
                propertiesHtml += this.getConditionProperties(node);
                break;
            case 'output_email':
                propertiesHtml += this.getEmailProperties(node);
                break;
            case 'output_slack':
                propertiesHtml += this.getSlackProperties(node);
                break;
            case 'output_jira':
                propertiesHtml += this.getJiraProperties(node);
                break;
        }

        this.propertiesPanel.innerHTML = propertiesHtml;

        // Attach event handlers for property changes
        this.attachPropertyHandlers(nodeId);
    }

    getAgentProperties(node) {
        return `
            <div class="mb-3">
                <label class="form-label small">Agent</label>
                <select class="form-select form-select-sm" id="prop-agent-id">
                    <option value="">Select agent...</option>
                </select>
            </div>
            <div class="mb-3">
                <label class="form-label small">Timeout (seconds)</label>
                <input type="number" class="form-control form-control-sm" id="prop-timeout"
                       value="${node.config.timeout_seconds || 300}" min="30" max="3600">
            </div>
        `;
    }

    getConditionProperties(node) {
        return `
            <div class="mb-3">
                <label class="form-label small">Field</label>
                <input type="text" class="form-control form-control-sm" id="prop-field"
                       value="${node.config.field || ''}" placeholder="e.g., severity">
            </div>
            <div class="mb-3">
                <label class="form-label small">Operator</label>
                <select class="form-select form-select-sm" id="prop-operator">
                    <option value="equals" ${node.config.operator === 'equals' ? 'selected' : ''}>Equals</option>
                    <option value="not_equals" ${node.config.operator === 'not_equals' ? 'selected' : ''}>Not Equals</option>
                    <option value="contains" ${node.config.operator === 'contains' ? 'selected' : ''}>Contains</option>
                    <option value="gt" ${node.config.operator === 'gt' ? 'selected' : ''}>Greater Than</option>
                    <option value="lt" ${node.config.operator === 'lt' ? 'selected' : ''}>Less Than</option>
                </select>
            </div>
            <div class="mb-3">
                <label class="form-label small">Value</label>
                <input type="text" class="form-control form-control-sm" id="prop-value"
                       value="${node.config.value || ''}" placeholder="e.g., critical">
            </div>
        `;
    }

    getEmailProperties(node) {
        return `
            <div class="mb-3">
                <label class="form-label small">To</label>
                <input type="text" class="form-control form-control-sm" id="prop-to"
                       value="${node.config.to || '{{user.email}}'}" placeholder="email@example.com">
            </div>
            <div class="mb-3">
                <label class="form-label small">Subject</label>
                <input type="text" class="form-control form-control-sm" id="prop-subject"
                       value="${node.config.subject || 'Workflow Result'}" placeholder="Subject">
            </div>
        `;
    }

    getSlackProperties(node) {
        return `
            <div class="mb-3">
                <label class="form-label small">Channel</label>
                <input type="text" class="form-control form-control-sm" id="prop-channel"
                       value="${node.config.channel || ''}" placeholder="#channel-name">
            </div>
        `;
    }

    getJiraProperties(node) {
        return `
            <div class="mb-3">
                <label class="form-label small">Project Key</label>
                <input type="text" class="form-control form-control-sm" id="prop-project"
                       value="${node.config.project_key || ''}" placeholder="e.g., NET">
            </div>
            <div class="mb-3">
                <label class="form-label small">Issue Type</label>
                <select class="form-select form-select-sm" id="prop-issue-type">
                    <option value="Task" ${node.config.issue_type === 'Task' ? 'selected' : ''}>Task</option>
                    <option value="Bug" ${node.config.issue_type === 'Bug' ? 'selected' : ''}>Bug</option>
                    <option value="Story" ${node.config.issue_type === 'Story' ? 'selected' : ''}>Story</option>
                </select>
            </div>
        `;
    }

    attachPropertyHandlers(nodeId) {
        const node = this.nodes.get(nodeId);
        if (!node) return;

        // Generic handler for all inputs
        this.propertiesPanel.querySelectorAll('input, select').forEach(input => {
            if (input.disabled) return;

            input.addEventListener('change', () => {
                const prop = input.id.replace('prop-', '').replace(/-/g, '_');
                node.config[prop] = input.value;
            });
        });
    }

    hideNodeProperties() {
        this.propertiesPanel.innerHTML = `
            <p class="text-muted small text-center py-4">Select a node to view properties</p>
        `;
    }

    applyTransform() {
        this.canvas.style.transform = `translate(${this.offset.x}px, ${this.offset.y}px) scale(${this.zoom})`;
        this.svg.style.transform = `translate(${this.offset.x}px, ${this.offset.y}px) scale(${this.zoom})`;
    }

    zoomIn() {
        this.zoom = Math.min(2, this.zoom + 0.1);
        this.applyTransform();
        document.getElementById('zoom-level').textContent = `${Math.round(this.zoom * 100)}%`;
    }

    zoomOut() {
        this.zoom = Math.max(0.25, this.zoom - 0.1);
        this.applyTransform();
        document.getElementById('zoom-level').textContent = `${Math.round(this.zoom * 100)}%`;
    }

    render() {
        this.renderConnections();
    }

    getDefinition() {
        const nodes = [];
        const edges = [];

        this.nodes.forEach(node => {
            nodes.push({
                id: node.id,
                type: node.type,
                position: { ...node.position },
                config: { ...node.config }
            });
        });

        this.edges.forEach(edge => {
            edges.push({
                from: edge.from,
                fromPort: edge.fromPort,
                to: edge.to,
                toPort: edge.toPort
            });
        });

        return { nodes, edges };
    }

    loadDefinition(definition) {
        // Clear existing nodes (except start)
        this.nodes.forEach((node, id) => {
            if (id !== 'start') {
                const element = document.getElementById(`node-${id}`);
                if (element) element.remove();
            }
        });

        this.nodes.clear();
        this.edges = [];

        // Load nodes
        if (definition.nodes) {
            definition.nodes.forEach(nodeDef => {
                if (nodeDef.type === 'start') {
                    // Update start node position
                    const startNode = document.getElementById('node-start');
                    if (startNode) {
                        startNode.style.left = `${nodeDef.position.x}px`;
                        startNode.style.top = `${nodeDef.position.y}px`;
                    }
                    this.nodes.set('start', {
                        id: 'start',
                        type: 'start',
                        position: nodeDef.position,
                        config: nodeDef.config || {}
                    });
                } else {
                    const id = nodeDef.id;
                    const match = id.match(/_(\d+)$/);
                    if (match) {
                        this.nodeIdCounter = Math.max(this.nodeIdCounter, parseInt(match[1]) + 1);
                    }

                    this.nodes.set(id, {
                        id: id,
                        type: nodeDef.type,
                        position: nodeDef.position,
                        config: nodeDef.config || {}
                    });

                    this.renderNode(this.nodes.get(id));
                }
            });
        }

        // Load edges
        if (definition.edges) {
            this.edges = definition.edges.map(e => ({ ...e }));
        }

        this.render();
    }
}

// Initialize builder when script loads
let workflowBuilder;
document.addEventListener('DOMContentLoaded', function() {
    workflowBuilder = new WorkflowBuilder();
});
