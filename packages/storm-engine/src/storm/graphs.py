"""Serializable directed graphs with plugin-declared port and configuration types."""
from storm.contracts import ValidationProblem


def ordered_nodes(graph):
    pending = {node['id']: set() for node in graph['nodes']}
    for edge in graph['edges']:
        source = edge['source'].split('.')[0]
        target = edge['target'].split('.')[0]
        pending[target].add(source)
    order = []
    while pending:
        ready = [node for node, parents in pending.items() if not parents]
        if not ready:
            raise ValueError('Graph contains a cycle')
        order.extend(ready)
        for node in ready:
            pending.pop(node)
        for parents in pending.values():
            parents.difference_update(ready)
    return order


def validate_graph(graph, descriptors):
    problems = []
    def add(code, node, field, message):
        problems.append(ValidationProblem(code, 'error', 'root', node, field, message))
    if (not isinstance(graph, dict) or graph.get('version') != '1'
            or not isinstance(graph.get('nodes'), list) or not graph['nodes']
            or not isinstance(graph.get('edges'), list)):
        add('graph.invalid', '', 'graph', 'Graph requires version 1, nodes and edges')
        return problems
    nodes, connected = {}, set()
    for node in graph['nodes']:
        if (not isinstance(node, dict) or not isinstance(node.get('id'), str)
                or not node['id'] or '.' in node['id'] or node['id'] in nodes):
            add('graph.node_id', '', 'nodes', 'Node IDs must be unique and contain no dots')
            continue
        name = node['id']
        nodes[name] = node
        descriptor = descriptors.get(node.get('type')) if isinstance(node.get('type'), str) else None
        if descriptor is None:
            add('graph.node_type', name, 'type', 'Select a registered node type')
            continue
        from storm.suite import Catalog
        try:
            Catalog._validate_value(name, node.get('config', {}), descriptor['schema'])
        except (TypeError, ValueError, KeyError) as error:
            add('graph.config', name, 'config', str(error))
    valid_edges = []
    for edge in graph['edges']:
        try:
            source, output = edge['source'].split('.')
            target, inlet = edge['target'].split('.')
            out_type = descriptors[nodes[source]['type']]['outputs'][output]
            in_type = descriptors[nodes[target]['type']]['inputs'][inlet]
        except (TypeError, AttributeError, ValueError, KeyError):
            add('graph.port', '', 'edges', 'Edge must connect declared node.port endpoints')
            continue
        if (target, inlet) in connected:
            add('graph.multiple_inputs', target, inlet, 'Input port accepts one edge')
        connected.add((target, inlet))
        if out_type != in_type:
            add('graph.port_type', target, inlet, 'Port types or declared dimensions do not match')
        valid_edges.append(edge)
    for name, node in nodes.items():
        for inlet in (descriptors.get(node.get('type'), {}) if isinstance(node.get('type'), str) else {}).get('inputs', {}):
            if (name, inlet) not in connected:
                add('graph.required_port', name, inlet, 'Connect the required input port')
    try:
        ordered_nodes({'nodes': list(nodes.values()), 'edges': valid_edges})
    except ValueError as error:
        add('graph.cycle', '', 'edges', str(error))
    return problems
