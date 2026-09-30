import uuid
from django.db import models


class Dataset(models.Model):
    name = models.CharField(max_length=160)
    created = models.DateTimeField(auto_now_add=True)


class DatasetAsset(models.Model):
    ROLE_CHOICES = [('pose', 'Pose'), ('roi', 'ROI'), ('video', 'Video'),
                    ('labels', 'Etiquetas')]
    dataset = models.ForeignKey(Dataset, on_delete=models.PROTECT, related_name='assets')
    role = models.CharField(max_length=16, choices=ROLE_CHOICES)
    original_name = models.CharField(max_length=255)
    relative_path = models.TextField()
    sha256 = models.CharField(max_length=64)
    size_bytes = models.PositiveBigIntegerField()
    session_id = models.CharField(max_length=160, blank=True)
    metadata = models.JSONField(default=dict)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(
            fields=('dataset', 'role', 'sha256'), name='unique_dataset_asset_content')]


class DatasetRevision(models.Model):
    dataset = models.ForeignKey(Dataset, on_delete=models.PROTECT, related_name='revisions')
    number = models.PositiveIntegerField()
    connector = models.CharField(max_length=80)
    asset_ids = models.JSONField(default=list)
    config = models.JSONField(default=dict)
    status = models.CharField(max_length=20, default='registered')
    inventory = models.JSONField(default=dict)
    artifact_ref = models.JSONField(null=True, blank=True)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('-number',)
        constraints = [models.UniqueConstraint(
            fields=('dataset', 'number'), name='unique_dataset_revision_number')]


class Project(models.Model):
    name = models.CharField(max_length=160)
    created = models.DateTimeField(auto_now_add=True)


class Study(models.Model):
    project = models.ForeignKey(Project, on_delete=models.PROTECT)
    name = models.CharField(max_length=160)
    created = models.DateTimeField(auto_now_add=True)
    archived_at = models.DateTimeField(null=True, blank=True, db_index=True)
    dataset_revision = models.ForeignKey(
        DatasetRevision, null=True, blank=True, on_delete=models.PROTECT,
        related_name='studies')
    adopted = models.ForeignKey('Job', null=True, blank=True, on_delete=models.PROTECT, related_name='+')


class ImmutableRevisionQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValueError('Revisions are immutable; create a successor')

    def bulk_update(self, objs, fields, batch_size=None):
        raise ValueError('Revisions are immutable; create a successor')

    def delete(self):
        raise ValueError('Revisions are immutable; create a successor')


class Revision(models.Model):
    objects = models.Manager.from_queryset(ImmutableRevisionQuerySet)()

    study = models.ForeignKey(Study, on_delete=models.PROTECT)
    kind = models.CharField(max_length=30)
    payload = models.JSONField()
    parent = models.ForeignKey('self', null=True, blank=True, on_delete=models.PROTECT)
    created = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValueError('Revisions are immutable; create a successor')
        return super().save(*args, **kwargs)

    def delete(self, using=None, keep_parents=False):
        raise ValueError('Revisions are immutable; create a successor')


class ArchivedPreparation(models.Model):
    """Reversible archive marker kept outside the immutable preparation revision."""

    preparation = models.OneToOneField(
        Revision, on_delete=models.PROTECT, related_name='archive_marker')
    archived_at = models.DateTimeField(auto_now_add=True)


class Job(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    revision = models.ForeignKey(Revision, on_delete=models.PROTECT)
    status = models.CharField(max_length=20, default='pending')
    progress = models.JSONField(default=dict, blank=True)
    result = models.JSONField(default=dict)
    error = models.TextField(blank=True)
    created = models.DateTimeField(auto_now_add=True)
    started = models.DateTimeField(null=True)
    finished = models.DateTimeField(null=True)
    previous = models.ForeignKey('self', null=True, blank=True, on_delete=models.PROTECT)
    source = models.ForeignKey('self', null=True, blank=True, on_delete=models.PROTECT, related_name='derived_jobs')
    operation = models.CharField(max_length=20, default='train')
