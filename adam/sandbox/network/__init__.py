"""
adam/sandbox/network

Host-side network interception and manipulation components for ADAM.
Provides proxy-based C2 response fabrication and network share interception.
"""

from __future__ import annotations

from adam.sandbox.network.interceptor import NetworkInterceptor

__all__ = ["NetworkInterceptor"]
