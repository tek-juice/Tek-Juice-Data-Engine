"""
DATA ENGINE — SEO Structured Data Validator & Injector
Phase 4: Validates Schema.org JSON-LD structured data against
Google's Rich Results requirements and injects it into content.
"""

import json
from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# Required fields per schema type for Google Rich Results
REQUIRED_FIELDS: dict[str, list[str]] = {
    "Article":              ["headline", "author", "datePublished"],
    "FAQPage":              ["mainEntity"],
    "HowTo":                ["name", "step"],
    "Product":              ["name"],
    "Organisation":         ["name"],
    "BreadcrumbList":       ["itemListElement"],
    "SoftwareApplication":  ["name", "applicationCategory", "operatingSystem"],
    "Dataset":              ["name", "description"],
    "WebPage":              ["name"],
    "Person":               ["name"],
}

RECOMMENDED_FIELDS: dict[str, list[str]] = {
    "Article":    ["description", "image", "publisher", "dateModified", "keywords"],
    "Product":    ["description", "image", "brand", "offers"],
    "HowTo":      ["description", "totalTime", "estimatedCost"],
    "FAQPage":    [],
    "WebPage":    ["description", "url", "inLanguage"],
}


@dataclass
class StructuredDataValidationResult:
    """Result of structured data validation."""
    schema_type: str
    is_valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    score: float = 0.0           # 0–100
    recommendations: list[str] = field(default_factory=list)


class StructuredDataValidator:
    """
    Validates JSON-LD structured data against Schema.org requirements
    and Google's Rich Results eligibility criteria.
    """

    def validate(self, schema: dict[str, Any]) -> StructuredDataValidationResult:
        """
        Validate a JSON-LD schema object.

        Args:
            schema: Parsed JSON-LD dict.

        Returns:
            StructuredDataValidationResult with errors, warnings, and score.
        """
        errors: list[str] = []
        warnings: list[str] = []

        # Check @context
        context = schema.get("@context", "")
        if "schema.org" not in str(context):
            errors.append("@context must reference 'https://schema.org'")

        # Check @type
        schema_type = schema.get("@type", "")
        if not schema_type:
            errors.append("@type is required.")
            return StructuredDataValidationResult(
                schema_type="Unknown", is_valid=False, errors=errors
            )

        # Check required fields
        required = REQUIRED_FIELDS.get(schema_type, [])
        for field_name in required:
            if field_name not in schema:
                errors.append(f"Required field missing: '{field_name}' (required for {schema_type})")

        # Check recommended fields
        recommended = RECOMMENDED_FIELDS.get(schema_type, [])
        for field_name in recommended:
            if field_name not in schema:
                warnings.append(f"Recommended field missing: '{field_name}' (improves rich results)")

        # Type-specific validation
        type_errors, type_warnings = self._validate_by_type(schema_type, schema)
        errors.extend(type_errors)
        warnings.extend(type_warnings)

        is_valid = len(errors) == 0
        score = max(0.0, 100.0 - (len(errors) * 20) - (len(warnings) * 5))

        recs = [f"Fix: {e}" for e in errors[:3]]
        recs += [f"Consider: {w}" for w in warnings[:3]]

        return StructuredDataValidationResult(
            schema_type=schema_type,
            is_valid=is_valid,
            errors=errors,
            warnings=warnings,
            score=round(score, 1),
            recommendations=recs,
        )

    def _validate_by_type(
        self, schema_type: str, schema: dict
    ) -> tuple[list[str], list[str]]:
        """Run type-specific validation rules."""
        errors, warnings = [], []

        if schema_type == "FAQPage":
            entities = schema.get("mainEntity", [])
            if not isinstance(entities, list) or len(entities) == 0:
                errors.append("FAQPage mainEntity must be a non-empty list of Question objects.")
            else:
                for i, q in enumerate(entities):
                    if q.get("@type") != "Question":
                        errors.append(f"mainEntity[{i}] must have @type: 'Question'")
                    if "acceptedAnswer" not in q:
                        errors.append(f"mainEntity[{i}] is missing 'acceptedAnswer'")

        elif schema_type == "HowTo":
            steps = schema.get("step", [])
            if not isinstance(steps, list) or len(steps) == 0:
                errors.append("HowTo must include at least one step.")

        elif schema_type == "Article":
            headline = schema.get("headline", "")
            if len(headline) > 110:
                warnings.append(
                    f"Article headline is {len(headline)} chars. "
                    "Google recommends under 110 chars."
                )

        return errors, warnings

    def validate_json_string(self, json_str: str) -> StructuredDataValidationResult:
        """Validate JSON-LD from a raw string."""
        try:
            schema = json.loads(json_str)
        except json.JSONDecodeError as exc:
            return StructuredDataValidationResult(
                schema_type="Unknown",
                is_valid=False,
                errors=[f"Invalid JSON: {exc}"],
            )
        return self.validate(schema)


class StructuredDataInjector:
    """Injects validated JSON-LD into HTML content."""

    def inject(self, html: str, schema: dict[str, Any]) -> str:
        """
        Inject a JSON-LD <script> tag into the HTML <head>.
        If no <head> tag is found, prepends to the HTML.
        """
        script_tag = (
            '<script type="application/ld+json">\n'
            + json.dumps(schema, indent=2, ensure_ascii=False)
            + "\n</script>"
        )

        if "</head>" in html:
            return html.replace("</head>", f"{script_tag}\n</head>", 1)
        return script_tag + "\n" + html
