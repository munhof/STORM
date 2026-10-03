from django import forms
from storm.suite import validate_data
from storm_studio.services import catalog


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    widget = MultipleFileInput

    def clean(self, data, initial=None):
        if not data:
            return super().clean(data, initial)
        files = data if isinstance(data, (list, tuple)) else [data]
        return [super(MultipleFileField, self).clean(item, initial) for item in files]


class DatasetUploadForm(forms.Form):
    pose_adapter = forms.ChoiceField(label='Formato de pose')
    pose_files = MultipleFileField(label='Archivos de pose',
                                   widget=MultipleFileInput(attrs={'accept': '.h5,.hdf,.hdf5,.csv'}))
    roi_files = MultipleFileField(label='Archivos de ROI', required=False,
                                  widget=MultipleFileInput(attrs={'accept': '.json'}))
    video_files = MultipleFileField(label='Videos', required=False,
                                    widget=MultipleFileInput(attrs={
                                        'accept': '.mp4,.avi,.mov,.mkv,.m4v,.mpeg,.mpg,.webm'}))
    label_files = MultipleFileField(label='Anotaciones', required=False,
                                    widget=MultipleFileInput(attrs={'accept': '.csv,.tsv,.json'}))
    fps = forms.FloatField(label='Cuadros por segundo', initial=30, min_value=0.001)
    hdf_key = forms.CharField(label='Clave HDF5', required=False)
    csv_frame_base = forms.IntegerField(
        label='Primer número de frame en el CSV', initial=1, required=False,
        help_text='Usá 1 si la primera fila anotada dice Frame 1; usá 0 si dice Frame 0.')
    pose_frame_base = forms.IntegerField(
        label='Primer frame del H5', initial=0, required=False,
        help_text='Usá 0 si el primer frame del H5 tiene índice 0; usá 1 si empieza en 1.')
    label_frame_reference = forms.ChoiceField(
        label='Las etiquetas CSV se refieren a', required=False, initial='pose',
        choices=[('pose', 'Frames de pose (H5)'), ('video', 'Frames de video')])

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        adapters = [(name, name.replace('_', ' '))
                    for name in catalog().connectors
                    if name not in {'numeric_json', 'json_records', 'prepared_artifact'}]
        self.fields['pose_adapter'].choices = adapters


class SessionPartitionForm(forms.Form):
    PARTITIONS = [('train', 'Entrenamiento'), ('validation', 'Validación'),
                  ('test', 'Evaluación'), ('unassigned', 'Sin asignar')]

    def __init__(self, *args, sessions, initial=None, reserved_ranges=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.sessions = list(sessions)
        initial = initial or {}
        reserved_ranges = reserved_ranges or {}
        for index, session_id in enumerate(self.sessions):
            default_partition = ('unassigned' if initial and session_id not in initial
                                 else 'train')
            self.fields[f'session_partition_{index}'] = forms.ChoiceField(
                label=session_id, choices=self.PARTITIONS,
                initial=initial.get(session_id, default_partition))
            interval = reserved_ranges.get(session_id, {})
            if not isinstance(interval, dict):
                interval = {}
            self.fields[f'reserved_start_{index}'] = forms.IntegerField(
                label='Desde (incluido)', required=False,
                initial=interval.get('start'))
            self.fields[f'reserved_stop_{index}'] = forms.IntegerField(
                label='Hasta (excluido)', required=False,
                initial=interval.get('stop'))

    def clean(self):
        result = super().clean()
        partitions = {
            session_id: result[f'session_partition_{index}']
            for index, session_id in enumerate(self.sessions)
            if f'session_partition_{index}' in result
        }
        if len(partitions) != len(self.sessions):
            raise forms.ValidationError('Asigná una partición a cada sesión.')
        if 'train' not in partitions.values():
            raise forms.ValidationError('Al menos una sesión debe quedar en entrenamiento.')
        reserved_ranges = {}
        for index, session_id in enumerate(self.sessions):
            start = result.get(f'reserved_start_{index}')
            stop = result.get(f'reserved_stop_{index}')
            if start is None and stop is None:
                continue
            if start is None or stop is None:
                raise forms.ValidationError(
                    f'Completá el inicio y el fin reservado para {session_id}.')
            if start < 0 or stop <= start:
                raise forms.ValidationError(
                    f'El rango reservado de {session_id} debe ser [inicio, fin), '
                    'con inicio no negativo y fin posterior.')
            reserved_ranges[session_id] = {'start': start, 'stop': stop}
        result['session_partitions'] = partitions
        result['reserved_evaluation_ranges'] = reserved_ranges
        return result


class AssetSessionForm(forms.Form):
    """Versioned links from registered video, ROI, and label files to pose sessions."""

    def __init__(self, *args, assets, pose_sessions, initial=None,
                 video_frame_offsets=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.assets = list(assets)
        self.pose_sessions = {str(key): value for key, value in pose_sessions.items()}
        initial = initial or {}
        video_frame_offsets = video_frame_offsets or {}
        session_choices = [(session, session) for session in dict.fromkeys(
            self.pose_sessions.values())]
        for asset in self.assets:
            if asset.role == 'pose':
                continue
            choices = [('', 'Sin vincular')]
            if asset.role == 'roi':
                choices.append(('global', 'Todas las sesiones'))
            choices.extend(session_choices)
            field_name = f'asset_session_{asset.pk}'
            selected = initial.get(str(asset.pk), '')
            if selected not in {value for value, _label in choices}:
                selected = ''
            self.fields[field_name] = forms.ChoiceField(
                label=f'{asset.get_role_display()} · {asset.original_name}',
                choices=choices, required=False, initial=selected)
            if asset.role == 'video':
                self.fields[f'asset_video_frame_offset_{asset.pk}'] = forms.IntegerField(
                    label='Desfase pose → video (frames)', required=False, initial=(
                        video_frame_offsets.get(str(asset.pk), 0)),
                    help_text='Frame de video = frame de pose + desfase.')

    def clean(self):
        result = super().clean()
        bindings = dict(self.pose_sessions)
        video_frame_offsets = {}
        for asset in self.assets:
            if asset.role == 'pose':
                continue
            bindings[str(asset.pk)] = result.get(f'asset_session_{asset.pk}', '') or ''
            if asset.role == 'video':
                offset = result.get(f'asset_video_frame_offset_{asset.pk}')
                video_frame_offsets[str(asset.pk)] = offset if offset is not None else 0

        for role in ('video', 'labels'):
            assigned = {}
            for asset in self.assets:
                if asset.role != role:
                    continue
                if role == 'labels' and asset.original_name.rsplit('.', 1)[-1].lower() != 'csv':
                    continue
                session = bindings.get(str(asset.pk))
                if not session or session == 'global':
                    continue
                if session in assigned:
                    self.add_error(None, f'Vinculá un solo archivo {role} a cada sesión.')
                    break
                assigned[session] = asset.pk
        result['asset_sessions'] = bindings
        result['video_frame_offsets'] = video_frame_offsets
        return result


class PreparationForm(forms.Form):
    name = forms.CharField(label='Nombre de la receta', max_length=160, initial='Preparación de pose')
    dataset_revision_id = forms.ChoiceField(label='Dataset de entrada')
    base_revision_id = forms.IntegerField(required=False, widget=forms.HiddenInput)
    steps = forms.JSONField(label='Pasos de procesamiento', initial=list, required=False,
                            widget=forms.HiddenInput)
    data = forms.JSONField(initial=dict, required=False, widget=forms.HiddenInput)

    def __init__(self, *args, **kwargs):
        dataset_revisions = kwargs.pop('dataset_revisions', ())
        super().__init__(*args, **kwargs)
        self.fields['dataset_revision_id'].choices = [
            (str(revision.pk),
             f'{revision.dataset.name} · revisión {revision.number} · {revision.connector}')
            for revision in dataset_revisions]

    def clean(self):
        result = super().clean()
        steps = result.get('steps') or []
        if not isinstance(steps, list):
            raise forms.ValidationError('Los pasos deben formar una lista.')
        available = catalog().steps.available
        for step in steps:
            if (not isinstance(step, dict) or
                    step.get('type') not in (*available, 'center')):
                raise forms.ValidationError('La pipeline contiene un paso desconocido.')
            if step['type'] == 'scale':
                from math import isfinite
                try:
                    factor = float(step['factor'])
                except (KeyError, TypeError, ValueError) as error:
                    raise forms.ValidationError('La escala requiere un factor numérico.') from error
                if not isfinite(factor):
                    raise forms.ValidationError('El factor de escala debe ser finito.')
            elif step['type'] == 'pose.recenter' and 'center_bodypart' in step.get('config', {}):
                config = step['config']
                if (not isinstance(config.get('center_bodypart'), str) or
                        not isinstance(config.get('bodyparts'), list) or
                        not config['bodyparts'] or
                        any(not isinstance(name, str) or not name for name in config['bodyparts'])):
                    raise forms.ValidationError('El recentrado requiere un origen y puntos corporales.')
            elif step['type'] == 'pose.orient_coordinates' and 'from_bodypart' in step.get('config', {}):
                config = step['config']
                source = config.get('from_bodypart')
                target = config.get('toward_bodypart')
                if (not isinstance(source, str) or not source or
                        not isinstance(target, str) or not target or source == target):
                    raise forms.ValidationError('La orientación requiere dos puntos corporales distintos.')
            elif step['type'] == 'pose.likelihood_filter' and 'bodyparts' in step.get('config', {}):
                from math import isfinite
                config = step['config']
                try:
                    threshold = float(config['threshold'])
                except (KeyError, TypeError, ValueError) as error:
                    raise forms.ValidationError('El filtro requiere un umbral de confianza.') from error
                if (not isfinite(threshold) or not 0 <= threshold <= 1 or
                        not isinstance(config['bodyparts'], list) or not config['bodyparts'] or
                        any(not isinstance(name, str) or not name for name in config['bodyparts'])):
                    raise forms.ValidationError('El filtro requiere umbral 0–1 y puntos corporales.')
            elif step['type'] not in {'center', 'scale'}:
                try:
                    catalog().steps.build(step['type'], step.get('config', {}))
                except (KeyError, TypeError, ValueError) as error:
                    raise forms.ValidationError(str(error)) from error
        result['steps'] = steps
        return result


class PlanForm(forms.Form):
    operation = forms.ChoiceField(label='Operación', choices=[('train', 'Entrenar / agrupar'),
                                                            ('infer', 'Inferir con adapter preentrenado')], required=False)
    model = forms.ChoiceField(label='Modelo')
    metrics = forms.MultipleChoiceField(
        label='Métricas', required=False, initial=[],
        help_text=('Si no elegís métricas, Studio usa las compatibles con la salida: '
                   'clasificación supervisada o agrupamiento.'),
        widget=forms.CheckboxSelectMultiple)
    connector = forms.ChoiceField(label='Conector', required=False, initial='numeric_json')
    dataset_revision_id = forms.ChoiceField(
        label='Fuente de datos registrada', required=False,
        choices=[('', 'Usar datos del plan')])
    label_correction_revision_id = forms.ChoiceField(
        label='Revisión de correcciones humanas', required=False,
        choices=[('', 'No incorporar correcciones')])
    preparation_revision_id = forms.ChoiceField(
        label='Receta de procesamiento', required=False,
        choices=[('', 'Sin receta guardada')])
    config = forms.JSONField(label='Configuración del modelo', initial=dict, required=False,
                             widget=forms.HiddenInput)
    data = forms.JSONField(label='Datos y particiones', initial={
        'inputs': [0, 1, 2, 3, 4, 5], 'targets': [0, 2, 4, 6, 8, 10],
        'train': [0, 1, 2, 3], 'test': [4, 5]}, required=False,
        widget=forms.HiddenInput)
    steps = forms.JSONField(label='Preparación secuencial', initial=list, required=False,
                            widget=forms.HiddenInput)
    branch_models = forms.MultipleChoiceField(
        label='Ramas de modelos', required=False,
        widget=forms.CheckboxSelectMultiple,
    )
    branch_configs = forms.JSONField(initial=dict, required=False, widget=forms.HiddenInput)
    seed = forms.IntegerField(initial=42)

    def __init__(self, *args, **kwargs):
        dataset_revisions = kwargs.pop('dataset_revisions', ())
        preparation_revisions = kwargs.pop('preparation_revisions', ())
        label_correction_revisions = kwargs.pop('label_correction_revisions', ())
        super().__init__(*args, **kwargs)
        self.fields['model'].choices = [(c['name'], c['name']) for c in catalog().describe()]
        self.fields['branch_models'].choices = self.fields['model'].choices
        self.fields['metrics'].choices = [(name, name) for name in catalog().metrics.available]
        self.fields['connector'].choices = [(name, name) for name in catalog().connectors]
        self.fields['dataset_revision_id'].choices = [('', 'Usar datos del plan')] + [
            (str(revision.pk),
             f'{revision.dataset.name} · revisión {revision.number} · {revision.connector}')
            for revision in dataset_revisions]
        self.fields['preparation_revision_id'].choices = [('', 'Sin receta guardada')] + [
            (str(revision.pk),
             f"{revision.payload.get('name', 'Procesamiento')} · "
             f"dataset r{revision.payload.get('dataset_revision_id', '?')}")
            for revision in preparation_revisions]
        self.fields['label_correction_revision_id'].choices = [
            ('', 'No incorporar correcciones')] + [
                (str(revision.pk),
                 f"Dataset r{revision.payload.get('dataset_revision_id', '?')} · "
                 f"revisión {revision.pk} · "
                 f"{len(revision.payload.get('corrections', {}))} etiquetas")
                for revision in label_correction_revisions]

    def clean_data(self):
        data = self.cleaned_data.get('data') or {}
        if self.cleaned_data.get('dataset_revision_id'):
            return data
        try:
            validate_data(data, require_train=self.data.get('operation', 'train') != 'infer',
                          numeric=self.data.get('connector', 'numeric_json') == 'numeric_json')
        except (ValueError, KeyError, TypeError) as error:
            raise forms.ValidationError(str(error))
        return data

    def clean(self):
        result = super().clean()
        result['operation'] = result.get('operation') or 'train'
        result['connector'] = result.get('connector') or 'numeric_json'
        result['dataset_revision_id'] = result.get('dataset_revision_id') or None
        correction_revision_id = result.get('label_correction_revision_id')
        result['label_correction_revision_id'] = (
            int(correction_revision_id) if correction_revision_id else None)
        result['preparation_revision_id'] = result.get('preparation_revision_id') or None
        if result.get('label_correction_revision_id') and result.get('operation') != 'train':
            self.add_error(
                'label_correction_revision_id',
                'Las correcciones se incorporan únicamente en un plan de entrenamiento nuevo.')
        if 'config' in result:
            result['config'] = result['config'] or {}
        if result.get('operation') == 'infer' and result.get('model'):
            capabilities = catalog().get(result['model']).capabilities
            if any(capability in capabilities for capability in ('train', 'group')):
                self.add_error(
                    'operation',
                    'Este modo instancia un adapter preentrenado. Para inferir con un modelo ajustado, abrí su corrida guardada en Modelos.',
                )
        if 'steps' in result:
            result['steps'] = result['steps'] or []
        if 'branch_models' in result:
            result['branch_models'] = [name for name in result['branch_models']
                                       if name != result.get('model')]
        if 'branch_configs' in result:
            branch_configs = result['branch_configs']
            if branch_configs is None:
                branch_configs = {}
            if not isinstance(branch_configs, dict):
                raise forms.ValidationError('La configuración de las ramas debe ser un objeto.')
            result['branch_configs'] = {
                name: branch_configs.get(name, {})
                for name in result.get('branch_models', [])
            }
        from storm.contracts import validate_plan
        from storm_studio.services import plan_data_summary

        worker_catalog = catalog()
        self.validation_problems = validate_plan(
            result, worker_catalog, plan_data_summary(result, worker_catalog))
        for problem in self.validation_problems:
            if problem.severity == 'error':
                self.add_error(None, f'{problem.branch}: {problem.message}')
        if not any(p.severity == 'error' for p in self.validation_problems):
            if result.get('model'):
                result['config'] = worker_catalog.normalize(result['model'], result.get('config', {}))
            result['branch_configs'] = {
                name: worker_catalog.normalize(name, result.get('branch_configs', {}).get(name, {}))
                for name in result.get('branch_models', [])}
        return result
