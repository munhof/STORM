"""Small model exercising real incremental and checkpoint contracts."""
from storm.models import ModelOutput


class OnlineMean:
    def __init__(self, config):
        self.total = 0.0
        self.count = 0
        self.cursor = 0

    def fit(self, inputs, targets=None):
        self.total, self.count, self.cursor = 0.0, 0, 0
        return self.partial_fit(inputs, targets)

    def partial_fit(self, inputs, targets=None):
        if targets is None or not len(targets) or len(inputs) != len(targets):
            raise ValueError('OnlineMean requires aligned targets')
        self.total += sum(float(value) for value in targets)
        self.count += len(targets)
        return self

    def fit_with_checkpoints(self, inputs, targets, checkpoint):
        if targets is None or len(inputs) != len(targets):
            raise ValueError('OnlineMean requires aligned targets')
        for position in range(self.cursor, len(targets)):
            self.total += float(targets[position])
            self.count += 1
            self.cursor = position + 1
            checkpoint(self.save_checkpoint())
        return self

    def save_checkpoint(self):
        return {'total': self.total, 'count': self.count, 'cursor': self.cursor}

    def load_checkpoint(self, checkpoint):
        self.total = checkpoint['total']
        self.count = checkpoint['count']
        self.cursor = checkpoint['cursor']

    def predict(self, inputs):
        if not self.count:
            raise ValueError('OnlineMean is not fitted')
        return ModelOutput([self.total / self.count for _ in inputs])
