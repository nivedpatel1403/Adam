"""
adam/db/interfaces.py

Interfaces for the Database Layer (ARCHITECTURE.md section 5.7).
"""

from abc import ABC, abstractmethod

from adam.contracts.interfaces import ArtifactRef
from adam.contracts.mutation import MutationResult
from adam.contracts.policy_decision import PolicyDecision
from adam.contracts.raw_event import RawEvent
from adam.contracts.semantic_event import SemanticEvent
from adam.contracts.session import AnalysisSession


class ISessionRepository(ABC):
    @abstractmethod
    async def create(self, session: AnalysisSession) -> None: ...
    
    @abstractmethod
    async def get_by_id(self, session_id: str) -> AnalysisSession | None: ...
    
    @abstractmethod
    async def update(self, session: AnalysisSession) -> None: ...
    
    @abstractmethod
    async def list_all(self) -> list[AnalysisSession]: ...


class IEventRepository(ABC):
    @abstractmethod
    async def create_raw(self, event: RawEvent) -> None: ...
    
    @abstractmethod
    async def get_raw_by_session(self, session_id: str) -> list[RawEvent]: ...
    
    @abstractmethod
    async def create_semantic(self, event: SemanticEvent) -> None: ...
    
    @abstractmethod
    async def get_semantic_by_session(self, session_id: str) -> list[SemanticEvent]: ...


class IDecisionRepository(ABC):
    @abstractmethod
    async def create(self, decision: PolicyDecision) -> None: ...
    
    @abstractmethod
    async def get_by_session(self, session_id: str) -> list[PolicyDecision]: ...


class IMutationRepository(ABC):
    @abstractmethod
    async def create(self, mutation: MutationResult) -> None: ...
    
    @abstractmethod
    async def get_by_session(self, session_id: str) -> list[MutationResult]: ...


class IArtifactRepository(ABC):
    @abstractmethod
    async def create(self, session_id: str, artifact: ArtifactRef) -> None: ...
    
    @abstractmethod
    async def get_by_session(self, session_id: str) -> list[ArtifactRef]: ...
