from abc import ABC, abstractmethod
from backend.app.contracts.schemas import ScanResult

class BaseAnalyzer(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        pass

    @abstractmethod
    def analyze(self, artifact_path: str) -> ScanResult:
        pass
