"""DeepEye plugin package — official backend shared by CTE + Full SQL arms.

Import surface:
  from workflows.mcts_v4.actions.deepeye_plugin import (
      build_official_schema_profile,
      official_schema_enabled,
      schema_profile_from_linked,
      revise_full_sql_official,
      vr_footer_enabled,
  )
"""

from .official_backend import (
    build_official_schema_profile,
    deepeye_root,
    ensure_deepeye_path,
    fit_prompt_with_official_schema_strip,
    official_revise_enabled,
    official_schema_enabled,
    revise_full_sql_official,
    schema_profile_from_linked,
    vr_footer_enabled,
)

__all__ = [
    "build_official_schema_profile",
    "deepeye_root",
    "ensure_deepeye_path",
    "fit_prompt_with_official_schema_strip",
    "official_revise_enabled",
    "official_schema_enabled",
    "revise_full_sql_official",
    "schema_profile_from_linked",
    "vr_footer_enabled",
]
