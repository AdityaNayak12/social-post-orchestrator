class TransientError(Exception):

    def __init__(self, message: str, stage: str):
        self.stage = stage
        super().__init__(message)

class DeterministicError(Exception):

    def __init__(self, message: str, stage: str):
        self.stage = stage
        super().__init__(message)
