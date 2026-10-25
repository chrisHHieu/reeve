"""Connector descriptors: the model, the credential checks, and the OAuth clients."""

from .model import ConnectorDescriptor, Field, MCPVariant, ValidationResult
from .oauth_clients import client_env

__all__ = ["ConnectorDescriptor", "Field", "MCPVariant", "ValidationResult", "client_env"]
