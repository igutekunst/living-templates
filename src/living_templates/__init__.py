"""Living Templates - A reactive file system for automatic template updates."""

__version__ = "0.1.0"
__author__ = "Isaac Harrison Gutekunst"
__email__ = "isaac@supercortex.io"

from .core.daemon import LivingTemplatesDaemon
from .core.models import NodeConfig, TemplateNode

__all__ = ["LivingTemplatesDaemon", "NodeConfig", "TemplateNode"] 