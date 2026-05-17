"""Contract resolver: validate → defaults → build_ast.

Four explicit layers:
1. validate_input — check params against schema (required fields, enums)
2. validate_no_extra_fields — reject unknown fields
3. apply_schema_defaults — fill missing params from schema defaults
4. build_ast — produce dynamic AST from contract + resolved params
"""

from app.contracts.skill_ir import SkillIR
from app.contracts.skill_registry import get_contract
from app.contract_resolver.validator import validate_input
from app.contract_resolver.defaults import apply_schema_defaults, validate_no_extra_fields
from app.contract_resolver.ast_builder import build_ast
from app.contract_resolver.models import ContractResolutionResult


def resolve(skill_ir: SkillIR) -> ContractResolutionResult:
    if skill_ir.contract_id is None:
        return ContractResolutionResult(ok=False, reason="no_contract")

    contract = get_contract(skill_ir.contract_id, skill_ir.version)
    if contract is None:
        return ContractResolutionResult(
            ok=False, reason=f"contract_not_found:{skill_ir.contract_id}@{skill_ir.version}"
        )

    ok, reason = validate_input(skill_ir.params, contract)
    if not ok:
        return ContractResolutionResult(ok=False, reason=reason)

    ok, reason = validate_no_extra_fields(skill_ir.params, contract.input_schema)
    if not ok:
        return ContractResolutionResult(ok=False, reason=reason)

    resolved_params = apply_schema_defaults(skill_ir.params, contract.input_schema)

    ast = build_ast(contract, resolved_params)

    return ContractResolutionResult(ok=True, ast=ast)
