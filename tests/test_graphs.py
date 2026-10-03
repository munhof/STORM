import json
from storm.contracts import ValidationProblem


def descriptors():
    return {'source': {'inputs': {}, 'outputs': {'out': 'pose'}, 'schema': {'type': 'object', 'properties': {}}},
            'model': {'inputs': {'in': 'pose'}, 'outputs': {'out': 'states'}, 'schema': {'type': 'object', 'properties': {}}}}


def test_graph_roundtrip_and_typed_ports():
    from storm.graphs import validate_graph, ordered_nodes
    graph = {'version': '1', 'nodes': [{'id': 'data', 'type': 'source'}, {'id': 'fit', 'type': 'model'}],
             'edges': [{'source': 'data.out', 'target': 'fit.in'}]}
    assert validate_graph(json.loads(json.dumps(graph)), descriptors()) == []
    assert ordered_nodes(graph) == ['data', 'fit']
    graph['edges'][0]['source'] = 'fit.out'
    problems = validate_graph(graph, descriptors())
    assert any(p.code == 'graph.port_type' for p in problems)
    assert any(p.code == 'graph.cycle' for p in problems)
    assert all(isinstance(p, ValidationProblem) for p in problems)


def test_graph_rejects_unknown_nodes_duplicate_inputs_and_missing_ports():
    from storm.graphs import validate_graph
    graph = {'version': '1', 'nodes': [{'id': 'fit', 'type': 'model'}], 'edges': []}
    assert any(p.code == 'graph.required_port' for p in validate_graph(graph, descriptors()))
    graph['nodes'][0]['type'] = 'unregistered'
    assert any(p.code == 'graph.node_type' for p in validate_graph(graph, descriptors()))
