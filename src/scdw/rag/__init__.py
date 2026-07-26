"""PLC XML 模板检索与生成产物管理。"""

from .retriever import get_template_xml, list_templates, save_generated_xml, search_templates

__all__ = ["get_template_xml", "list_templates", "save_generated_xml", "search_templates"]
