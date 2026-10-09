"""Observation-preserving feature extraction, without learned statistics."""
import math
from dataclasses import replace
from numbers import Real

from storm.experiments import NodeOperation
from storm.pipeline.context import ContentDescriptor


def _vector(row):
    try:
        values = list(row)
    except TypeError as error:
        raise ValueError('Expected a numeric feature vector') from error
    if not values or any(isinstance(v, bool) or not isinstance(v, Real) or not math.isfinite(v) for v in values):
        raise ValueError('Features require finite numeric coordinates; prepare missing values explicitly')
    return values


def _result(context, values, names, operation, config):
    units = context.metadata.get('units')
    if 'data' in context.schema:
        units = context.schema['data'].units or units
    context.data = values
    context.metadata['feature_names'] = names
    context.metadata.setdefault('feature_extraction', []).append({'operation': operation, 'config': dict(config)})
    original = context.schema.get('data', ContentDescriptor('number', identity='metadata.observation_ids'))
    context.schema['data'] = replace(original, dtype='number', units=units,
        dimensions=('observations', len(names)))
    return {'context': context}


def distance(inputs, config):
    context = inputs['context']
    first, second = config['first'], config['second']
    if len(first) != len(second) or len(first) not in (2, 3) or any(
            type(i) is not int or i < 0 for i in [*first, *second]):
        raise ValueError('Choose matching 2D or 3D coordinate column indices, starting at zero')
    if len(set(first)) != len(first) or len(set(second)) != len(second):
        raise ValueError('Coordinate axes must use distinct columns')
    values = []
    for row in context.data:
        row = _vector(row)
        if max(*first, *second) >= len(row):
            raise ValueError('Coordinate column is absent from the input')
        values.append([math.dist([row[i] for i in first], [row[i] for i in second])])
    return _result(context, values, [config['name']], 'features.distance', config)


def window_statistics(inputs, config):
    context = inputs['context']
    statistic = config['statistic']
    if statistic not in ('mean', 'std', 'min', 'max'):
        raise ValueError('Unknown window statistic')
    values, width = [], None
    for window in context.data:
        rows = [_vector(row) for row in window]
        if not rows:
            raise ValueError('Empty windows cannot produce features')
        width = width or len(rows[0])
        if any(len(row) != width for row in rows):
            raise ValueError('Window feature dimensions must match')
        def aggregate(column):
            mean = math.fsum(column) / len(column)
            if statistic == 'mean':
                return mean
            if statistic == 'std':
                return math.sqrt(math.fsum((v - mean) ** 2 for v in column) / len(column))
            return min(column) if statistic == 'min' else max(column)
        values.append([aggregate(column) for column in zip(*rows)])
    names = context.metadata.get('feature_names', [])
    if width is not None and len(names) != width:
        names = [f'feature_{i}' for i in range(width)]
    return _result(context, values, [f'{name}_{statistic}' for name in names],
                   'features.window_statistics', config)


def feature_operations():
    indices = {'type': 'array', 'items': {'type': 'integer', 'minimum': 0}, 'minItems': 2, 'maxItems': 3}
    for name, label, properties, execute, description in (
        ('features.distance', 'Distancia entre puntos', {
            'first': {**indices, 'title': 'Columnas del primer punto (desde 0)', 'default': [0, 1]},
            'second': {**indices, 'title': 'Columnas del segundo punto (desde 0)', 'default': [2, 3]},
            'name': {'type': 'string', 'title': 'Nombre de la característica', 'default': 'distance'}},
            distance, 'Distancia euclídea 2D o 3D por observación, en las unidades de entrada.'),
        ('features.window_statistics', 'Estadísticas por ventana', {
            'statistic': {'type': 'string', 'title': 'Estadística', 'enum': ['mean', 'std', 'min', 'max'], 'default': 'mean'}},
            window_statistics, 'Una característica por columna de cada ventana. std usa desviación poblacional. Requiere ventanas [tiempo, características].'),
    ):
        labels = {key: value.pop('title') for key, value in properties.items()}
        yield NodeOperation(name, '1', {'context': 'context'}, {'context': 'context'},
            {'type': 'object', 'properties': properties, 'required': list(properties)}, execute,
            kind='transform', reads=('data', 'metadata', 'schema'),
            writes=('data', 'metadata.feature_names', 'metadata.feature_extraction', 'schema.data'),
            descriptor={'category': 'features', 'label': label, 'description': description, 'field_labels': labels})


# The recipe editor uses the same extractors as experiment graphs.
from storm.pipeline.step import PipelineStep
from storm.pipeline import PipelineContext


class DistanceFeatureStep(PipelineStep):
    step_type = 'features.distance'

    def __init__(self, first=None, second=None, name='distance'):
        self.config = {'first': [0, 1] if first is None else first,
                       'second': [2, 3] if second is None else second, 'name': name}
        if not isinstance(name, str) or not name.strip():
            raise ValueError('A feature name is required')
        distance({'context': PipelineContext(data=[])}, self.config)

    def process(self, context):
        return distance({'context': context}, self.config)['context']


class WindowStatisticsStep(PipelineStep):
    step_type = 'features.window_statistics'

    def __init__(self, statistic='mean'):
        self.config = {'statistic': statistic}
        window_statistics({'context': PipelineContext(data=[])}, self.config)

    def process(self, context):
        return window_statistics({'context': context}, self.config)['context']


def rainstorm_supervised(inputs, config):
    """Prepare the declared 6-bodypart RAINSTORM supervised input shape."""
    context = inputs['context']
    names = context.metadata.get('feature_names', [])
    bodyparts = config['bodyparts']
    if len(bodyparts) != 6 or len(set(bodyparts)) != 6:
        raise ValueError('Elegí los seis puntos corporales en el orden de entrada del modelo')
    columns = [[names.index(f'{part}_{axis}') if f'{part}_{axis}' in names else -1
                for axis in ('x', 'y')] for part in bodyparts]
    if any(index < 0 for pair in columns for index in pair):
        raise ValueError('El dataset no contiene las coordenadas x/y de los seis puntos elegidos')
    rows = [_vector(row) for row in context.data]
    if any(max(index for pair in columns for index in pair) >= len(row) for row in rows):
        raise ValueError('Las coordenadas declaradas no coinciden con la forma de los datos')
    points = [[[row[index] for index in pair] for pair in columns] for row in rows]
    center = config.get('center_bodypart')
    if center:
        if center not in bodyparts:
            raise ValueError('El punto para centrar debe estar entre los seis puntos elegidos')
        ci = bodyparts.index(center)
        points = [[[xy[axis] - row[ci][axis] for axis in range(2)] for xy in row]
                  for row in points]
    orientation = config.get('orientation') or []
    if orientation:
        if len(orientation) != 2 or orientation[0] == orientation[1] or any(bp not in bodyparts for bp in orientation):
            raise ValueError('La orientación requiere dos puntos distintos de los seis elegidos')
        south, north = (bodyparts.index(bp) for bp in orientation)
        for row in points:
            dx, dy = row[north][0]-row[south][0], row[north][1]-row[south][1]
            if dx == 0 and dy == 0:
                raise ValueError('Las referencias de orientación coinciden')
            angle = math.pi/4 - math.atan2(-dy, -dx)
            cosine, sine = math.cos(angle), math.sin(angle)
            for point in row:
                x, y = point
                point[0], point[1] = x*cosine-y*sine, x*sine+y*cosine
    flat = [[coordinate for point in row for coordinate in point] for row in points]
    offsets = config['offsets']
    if any(type(offset) is not int for offset in offsets) or offsets != sorted(set(offsets)) or 0 not in offsets:
        raise ValueError('Los offsets deben ser únicos, ordenados e incluir cero')
    metadata = context.metadata
    ids, sessions = metadata.get('observation_ids', []), metadata.get('sessions', [])
    frames, partitions = metadata.get('frames', metadata.get('times', [])), metadata.get('partitions', [])
    segments = metadata.get('segments', sessions)
    count = len(rows)
    if any(len(value) != count for value in (ids, sessions, frames, partitions, segments)):
        raise ValueError('La preparación supervisada requiere identidad, sesión, frame, segmento y partición alineados')
    lookup = {(sessions[i], segments[i], partitions[i], frames[i]): i for i in range(count)}
    if len(lookup) != count:
        raise ValueError('Frames repetidos impiden formar ventanas sin ambigüedad')
    selected, output = [], []
    for i in range(count):
        if any((sessions[i], segments[i], partitions[i], frames[i]+offset) not in lookup
               for offset in range(offsets[0], offsets[-1]+1)):
            continue
        parents = [lookup.get((sessions[i], segments[i], partitions[i], frames[i]+offset)) for offset in offsets]
        if any(parent is None for parent in parents):
            continue
        selected.append(i)
        window = [flat[parent] for parent in parents]
        output.append(window[0] if offsets == [0] else window)
    context.data = output
    if context.targets is not None:
        context.targets = [context.targets[i] for i in selected]
    for key in ('observation_ids','sessions','frames','times','segments','partitions','observation_indices'):
        if key in metadata:
            metadata[key] = [metadata[key][i] for i in selected]
    context.state['window_parents'] = [[ids[i] for i in [lookup[(sessions[j],segments[j],partitions[j],frames[j]+offset)] for offset in offsets]] for j in selected]
    metadata['feature_names'] = [f'{part}_{axis}' for part in bodyparts for axis in ('x','y')]
    metadata['units'] = metadata.get('units', 'unidades de entrada')
    metadata['prepared_steps'] = list(metadata.get('prepared_steps', []))+['rainstorm.supervised_features.v1']
    dimensions = ('observations', 12) if offsets == [0] else ('observations', len(offsets), 12)
    context.schema['data'] = ContentDescriptor('number', dimensions,
        units=metadata['units'], granularity='window' if len(offsets)>1 else 'observation',
        identity='metadata.observation_ids', time='metadata.frames')
    return {'context': context}


def supervised_feature_operation():
    from storm.experiments import NodeOperation
    return NodeOperation('features.rainstorm_supervised', '1', {'context':'context'}, {'context':'context'},
        {'type':'object','properties':{
            'bodyparts':{'type':'array','items':{'type':'string'},'minItems':6,'maxItems':6},
            'offsets':{'type':'array','items':{'type':'integer'},'minItems':1,'default':[0]},
            'center_bodypart':{'type':'string','default':''},
            'orientation':{'type':'array','items':{'type':'string'},'maxItems':2,'default':[]}},
         'required':['bodyparts']},rainstorm_supervised,reads=('data','metadata'),
         writes=('data','targets','metadata','schema','state.window_parents'),
         descriptor={'category':'features','label':'Características supervisadas RAINSTORM',
            'description':'Seis puntos corporales en orden explícito, centrado y orientación opcionales y ventanas completas dentro de sesión, segmento y partición.'})
