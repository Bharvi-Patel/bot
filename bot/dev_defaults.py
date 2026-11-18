"""The one place that still names South Jackson Furniture.

This is the settings used by local development, chat_cli, evals and the existing tests when no config source is
configured. A production, multi-store server must NOT rely on it: it should load every store from a ConfigSource
(see bot/store_config.py), so a store without settings is refused instead of answering as South Jackson.
"""
from bot.store_config import StoreConfig

SOUTH_JACKSON = StoreConfig(
    store_uuid="dev",
    store_name="South Jackson Furniture",
    allowed_domains=("southjacksonfurniture.com",),
    vertical="furniture",
    shipping_example='We currently ship only within the USA',
    scrub_terms=("brooklyn", "jamaica"),      # the Financing page names other showrooms
    catalog_rules=(
        'If a category title joins several things (for example "Rugs and Decor", which also holds sculptures and accent tables), do not search it. Use its child category whose title is just the product type (for example "Rugs", slug rugs-and-decor-rugs), and never list sculptures, tables or other items as rugs. If list_categories shows has_subcategories true for a category that fits, search that category rather than a broader parent.',
    ),
)
