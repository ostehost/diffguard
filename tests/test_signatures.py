"""Tests for signature comparison."""

import pytest

from diffguard.engine.signatures import (
    assess_signature_change,
    compare_signatures,
    extract_params,
    is_default_value_change,
    is_breaking_change,
)


class TestExtractParams:
    def test_simple(self) -> None:
        assert extract_params("def foo(a, b)") == ["a", "b"]

    def test_empty(self) -> None:
        assert extract_params("def foo()") == []

    def test_self_filtered(self) -> None:
        assert extract_params("def foo(self, a)") == ["a"]

    def test_typed(self) -> None:
        assert extract_params("def foo(a: int, b: str)") == ["a: int", "b: str"]

    def test_defaults(self) -> None:
        params = extract_params('def foo(a: int, b: str = "x")')
        assert len(params) == 2

    def test_nested_parens_callable(self) -> None:
        """Regression: nested brackets/parens in Callable types."""
        params = extract_params("def foo(a: Callable[[int], str], b: int)")
        assert params == ["a: Callable[[int], str]", "b: int"]

    def test_nested_parens_complex(self) -> None:
        params = extract_params("def foo(a: dict[str, list[int]], b: tuple[int, ...])")
        assert params == ["a: dict[str, list[int]]", "b: tuple[int, ...]"]

    def test_return_type_complex(self) -> None:
        """Regression: complex return types should not be truncated."""
        from diffguard.engine.signatures import _extract_return_type

        assert _extract_return_type("def foo() -> dict[str, int]") == "dict[str, int]"

    def test_dict_literal_default(self) -> None:
        params = extract_params('def foo(x: dict = {"a": 1, "b": 2}, y: int = 0)')
        assert params == ['x: dict = {"a": 1, "b": 2}', "y: int = 0"]

    def test_string_commas_and_lambda_headers_do_not_split_parameters(self) -> None:
        assert extract_params('def foo(value="a,b", other=1)') == [
            'value="a,b"',
            "other=1",
        ]
        assert extract_params("def foo(callback=lambda a, b: a + b, other=1)") == [
            "callback=lambda a, b: a + b",
            "other=1",
        ]

    def test_typescript_generic_type_commas_do_not_split_parameters(self) -> None:
        assert extract_params(
            "function foo(value: Map<string, number>, other: boolean)",
            "typescript",
        ) == ["value: Map<string, number>", "other: boolean"]

    def test_typescript_generic_function_type_commas_do_not_split_parameters(self) -> None:
        assert extract_params(
            "function foo(cb: <T, U>(x: T) => U, other: boolean)",
            "typescript",
        ) == ["cb: <T, U>(x: T) => U", "other: boolean"]

    def test_typescript_arrow_inside_generic_does_not_close_outer_angle_group(self) -> None:
        assert extract_params(
            "function foo(value: Either<(x: T) => U, Error>, other: boolean)",
            "typescript",
        ) == ["value: Either<(x: T) => U, Error>", "other: boolean"]

    def test_typescript_instantiation_expression_commas_do_not_split_parameters(self) -> None:
        assert extract_params(
            "function foo(cb = makePair<string, number>, other = true)",
            "typescript",
        ) == ["cb = makePair<string, number>", "other = true"]

    def test_typescript_generic_constraint_parentheses_precede_outer_parameters(self) -> None:
        assert extract_params(
            "function foo<T extends (x: Map<string, Array<number>>) => Promise<void>>"
            "(first: number, second: string)",
            "typescript",
        ) == ["first: number", "second: string"]
        assert extract_params(
            "<T extends (x: string) => void>(first: T, second: boolean) => void",
            "typescript",
        ) == ["first: T", "second: boolean"]

    def test_go_type_constraint_parentheses_precede_outer_parameters(self) -> None:
        assert extract_params(
            "func foo[T interface{ M(func(int, string)); N() }](first int, second string)",
            "go",
        ) == ["first int", "second string"]
        assert extract_params(
            "func (server *Server[T]) Handle(first int, second string)",
            "go",
        ) == ["first int", "second string"]

    def test_typescript_comparison_default_is_not_a_generic_group(self) -> None:
        assert extract_params(
            "function foo(value = left < right, other = true)",
            "typescript",
        ) == ["value = left < right", "other = true"]
        assert extract_params(
            "function foo(value = lower<current, other = current>lower)",
            "typescript",
        ) == ["value = lower<current", "other = current>lower"]

    def test_typescript_regex_delimiters_do_not_split_parameters(self) -> None:
        assert extract_params(
            "function f(value=/[),]/,next=1):number",
            "typescript",
        ) == ["value=/[),]/", "next=1"]

    def test_typescript_nested_templates_keep_parameter_and_tail_boundaries(self) -> None:
        from diffguard.engine.signatures import _signature_tail

        signature = (
            "function f(value=`outer ${`inner,),${/[},)]/.source}`}`,next=1):Promise<string>"
        )

        assert extract_params(signature, "typescript") == [
            "value=`outer ${`inner,),${/[},)]/.source}`}`",
            "next=1",
        ]
        assert _signature_tail(signature, "typescript") == ":Promise<string>"

    def test_non_python_comment_contents_are_opaque_to_structural_scanning(self) -> None:
        from diffguard.engine.signatures import _signature_tail

        signature = "function f(value=1 /* ), `literal`, /[)]/ */,next=2):Promise<string>"

        assert extract_params(signature, "typescript") == [
            "value=1 /* ), `literal`, /[)]/ */",
            "next=2",
        ]
        assert _signature_tail(signature, "typescript") == ":Promise<string>"

    @pytest.mark.parametrize(
        "signature",
        [
            "function f(cb=()=>{{}/[),]/.test(text)},other=1):number",
            "function f(cb=async x=>await /[)]/.test(x),other=1):number",
            "function f(cb=async x=>fn(await /a b/,await /[)]/),other=1):number",
            "function f(cb={async m(){return await /[)]/.test(x)}},other=1):number",
            "function f(cb={async m(){return call(first,nested(await /[)]/))}},other=1):number",
            "function f(cb=function(){{}/[),]/.test(text)},other=1):number",
        ],
    )
    def test_typescript_regex_after_statement_goals_keeps_outer_boundaries(
        self,
        signature: str,
    ) -> None:
        from diffguard.engine.signatures import _signature_tail

        params = extract_params(signature, "typescript")

        assert len(params) == 2
        assert params[1] == "other=1"
        assert _signature_tail(signature, "typescript") == ":number"


class TestIsBreakingChange:
    def test_identical(self) -> None:
        assert is_breaking_change("def foo(a: int)", "def foo(a: int)") is False

    def test_param_added_no_default(self) -> None:
        assert is_breaking_change("def foo(a: int)", "def foo(a: int, b: str)") is True

    def test_param_removed(self) -> None:
        assert is_breaking_change("def foo(a: int, b: str)", "def foo(a: int)") is True

    def test_param_type_changed(self) -> None:
        assert is_breaking_change("def foo(a: int)", "def foo(a: str)") is False
        assessment = assess_signature_change("def foo(a: int)", "def foo(a: str)", "python")
        assert assessment.category_id == "parameter_annotation_changed"
        assert assessment.breaking is None

    def test_param_added_with_default(self) -> None:
        assert is_breaking_change("def foo(a: int)", 'def foo(a: int, b: str = "x")') is False

    def test_return_type_changed(self) -> None:
        assert is_breaking_change("def foo(a: int) -> int", "def foo(a: int) -> str") is False
        assessment = assess_signature_change(
            "def foo(a: int) -> int", "def foo(a: int) -> str", "python"
        )
        assert assessment.category_id == "return_annotation_changed"
        assert assessment.breaking is None

    def test_return_type_added(self) -> None:
        # Adding a return type when there was none — conservative, not breaking
        assert is_breaking_change("def foo(a: int)", "def foo(a: int) -> int") is False

    def test_default_removed(self) -> None:
        assessment = assess_signature_change("def foo(a=1)", "def foo(a)", "python")
        assert assessment.category_id == "default_removed"
        assert assessment.breaking is True

    def test_default_removal_precedes_compound_parameter_kind_change(self) -> None:
        assessment = assess_signature_change(
            "def foo(value=1, /)",
            "def foo(value)",
            "python",
        )
        assert assessment.rule_id == "DG104"
        assert assessment.category_id == "default_removed"
        assert assessment.breaking is True

    def test_proven_default_removal_retains_secondary_type_evidence(self) -> None:
        assessment = assess_signature_change(
            "def foo(value: int = 1) -> int",
            "def foo(value: str) -> str",
            "python",
        )

        assert assessment.rule_id == "DG104"
        assert assessment.category_id == "default_removed"
        assert assessment.breaking is True
        assert any("Default removed" in item for item in assessment.evidence)
        assert any("parameter 'value'" in item for item in assessment.evidence)
        assert any("return annotation" in item for item in assessment.evidence)
        assert any("type-checker" in item for item in assessment.analysis_gaps)

    def test_default_removal_precedes_compound_parameter_additions(self) -> None:
        signatures = (
            ("def foo(value=1)", "def foo(value, optional=2)"),
            ("def foo(value=1)", "def foo(value, required)"),
            (
                "def foo(value=1, *, old=2)",
                "def foo(value, *, replacement=2, extra=3)",
            ),
        )

        for old_signature, new_signature in signatures:
            assessment = assess_signature_change(old_signature, new_signature, "python")
            assert assessment.rule_id == "DG104"
            assert assessment.category_id == "default_removed"
            assert assessment.breaking is True

    @pytest.mark.parametrize(
        ("old_signature", "new_signature"),
        [
            (
                "def foo(a=1, b=2, *args, **kwargs)",
                "def foo(a, *args, **kwargs)",
            ),
            (
                "def foo(keep=1, drop=2, /, *args)",
                "def foo(keep, /, *args)",
            ),
            (
                "def foo(*args, keep=1, drop=2, **kwargs)",
                "def foo(*args, keep, **kwargs)",
            ),
            (
                "def foo(a=1, b=2, c=3, *args, **kwargs)",
                "def foo(a, *args, **kwargs)",
            ),
        ],
    )
    def test_default_removal_precedes_fully_captured_peer_removal(
        self,
        old_signature: str,
        new_signature: str,
    ) -> None:
        assessment = assess_signature_change(old_signature, new_signature, "python")

        assert assessment.rule_id == "DG104"
        assert assessment.category_id == "default_removed"
        assert assessment.breaking is True

    def test_default_changed_is_behavioral(self) -> None:
        assessment = assess_signature_change("def foo(a=1)", "def foo(a=2)", "python")
        assert assessment.category_id == "default_changed"
        assert assessment.breaking is False

    def test_compound_default_and_annotation_changes_preserve_all_evidence(self) -> None:
        assessment = assess_signature_change(
            "def foo(a: int = 1) -> int",
            "def foo(a: str = 2) -> str",
            "python",
        )

        assert assessment.rule_id == "DG110"
        assert assessment.category_id == "signature_changed"
        assert assessment.breaking is None
        assert any("Default changed" in item for item in assessment.evidence)
        assert any("parameter 'a'" in item for item in assessment.evidence)
        assert any("return annotation" in item for item in assessment.evidence)
        assert any("Compound signature" in item for item in assessment.analysis_gaps)
        assert any("Runtime behavior" in item for item in assessment.analysis_gaps)
        assert any("type-checker" in item for item in assessment.analysis_gaps)

    def test_multiple_changes_in_one_signature_category_keep_the_rule(self) -> None:
        assessment = assess_signature_change(
            "def foo(a=1, b=2)",
            "def foo(a=3, b=4)",
            "python",
        )

        assert assessment.rule_id == "DG105"
        assert assessment.category_id == "default_changed"
        assert assessment.breaking is False
        assert len(assessment.evidence) == 2
        assert any("parameter 'a'" in item for item in assessment.evidence)
        assert any("parameter 'b'" in item for item in assessment.evidence)

    def test_typescript_compatibility_is_not_claimed(self) -> None:
        assessment = assess_signature_change(
            "function foo(a: number)", "function foo(a: number, b: string)", "typescript"
        )
        assert assessment.category_id == "parameter_added"
        assert assessment.breaking is None
        assert assessment.analysis_gaps

    def test_typescript_generic_type_comma_does_not_invent_parameter_removal(self) -> None:
        assessment = assess_signature_change(
            "function foo(value: Map<string, number>)",
            "function foo(value: Set<string>)",
            "typescript",
        )
        assert assessment.category_id == "parameters_changed"
        assert assessment.breaking is None

    def test_typescript_generic_function_type_is_one_outer_parameter(self) -> None:
        assessment = assess_signature_change(
            "function foo(cb: <T, U>(x: T) => U, enabled: boolean)",
            "function foo(cb: <T, U>(x: T) => Promise<U>, enabled: boolean)",
            "typescript",
        )
        assert assessment.category_id == "parameters_changed"
        assert assessment.breaking is None

    def test_typescript_arrow_in_generic_does_not_invent_parameter_removal(self) -> None:
        assessment = assess_signature_change(
            "function foo(value: Either<(x: T) => U, Error>, other: boolean)",
            "function foo(value: Either<Handler<T, U>, Error>, other: boolean)",
            "typescript",
        )

        assert assessment.category_id == "parameters_changed"
        assert assessment.breaking is None

    def test_typescript_instantiation_expression_change_keeps_outer_arity(self) -> None:
        assessment = assess_signature_change(
            "function foo(cb = makePair<string>, enabled = true)",
            "function foo(cb = makePair<string, number>, enabled = true)",
            "typescript",
        )

        assert assessment.category_id == "parameters_changed"
        assert assessment.breaking is None

    def test_go_compatibility_is_not_claimed(self) -> None:
        assessment = assess_signature_change("func foo(a int)", "func foo(a string)", "go")
        assert assessment.breaking is None
        assert assessment.analysis_gaps

    def test_variadic_parameter_addition_is_not_required(self) -> None:
        assessment = assess_signature_change("def foo(a)", "def foo(a, *args)", "python")
        assert assessment.category_id == "optional_parameter_added"
        assert assessment.breaking is False

    def test_variadic_addition_does_not_hide_retained_parameter_kind_change(self) -> None:
        assessment = assess_signature_change(
            "def foo(value)",
            "def foo(*args, value)",
            "python",
        )
        assert assessment.category_id == "parameter_kind_changed"
        assert assessment.breaking is True

    def test_double_star_variadic_parameter_addition_is_not_required(self) -> None:
        assessment = assess_signature_change("def foo(a)", "def foo(a, **kwargs)", "python")
        assert assessment.category_id == "optional_parameter_added"
        assert assessment.breaking is False

    @pytest.mark.parametrize(
        ("old_signature", "new_signature"),
        [
            ("def foo(value, /, *args)", "def foo(*args)"),
            ("def foo(*, option=1, **kwargs)", "def foo(**kwargs)"),
            (
                "def foo(value=1, *args, **kwargs)",
                "def foo(*args, **kwargs)",
            ),
        ],
    )
    def test_parameter_removal_fully_captured_by_variadics_is_not_breaking(
        self,
        old_signature: str,
        new_signature: str,
    ) -> None:
        assessment = assess_signature_change(old_signature, new_signature, "python")

        assert assessment.category_id == "parameter_removed"
        assert assessment.breaking is False
        assert assessment.analysis_gaps

    @pytest.mark.parametrize(
        ("old_signature", "new_signature"),
        [
            ("def foo(value, *args)", "def foo(*args)"),
            ("def foo(value, **kwargs)", "def foo(**kwargs)"),
        ],
    )
    def test_partial_variadic_capture_does_not_hide_a_removed_call_mode(
        self,
        old_signature: str,
        new_signature: str,
    ) -> None:
        assessment = assess_signature_change(old_signature, new_signature, "python")

        assert assessment.category_id == "parameter_removed"
        assert assessment.breaking is True

    def test_compound_removal_does_not_hide_retained_kind_call_shape_break(self) -> None:
        old_signature = "def f(a, /, *, b, **kwargs)"
        new_signature = "def f(a, **kwargs)"

        assessment = assess_signature_change(old_signature, new_signature, "python")

        assert assessment.category_id == "parameter_removed"
        assert assessment.breaking is True

        def old_function(a: int, /, *, b: int, **kwargs: int) -> object:
            return a, b, kwargs

        def new_function(a: int, **kwargs: int) -> object:
            return a, kwargs

        assert old_function(1, b=2, a=3) == (1, 2, {"a": 3})
        with pytest.raises(TypeError, match="multiple values"):
            new_function(1, **{"b": 2, "a": 3})

    def test_nontrailing_captured_positional_removal_remains_unknown(self) -> None:
        assessment = assess_signature_change(
            "def foo(first, second, /, *args)",
            "def foo(second, /, *args)",
            "python",
        )

        assert assessment.category_id == "parameter_removed"
        assert assessment.breaking is None
        assert assessment.analysis_gaps

    def test_defaulted_positional_addition_cannot_steal_variadic_capture(self) -> None:
        assessment = assess_signature_change(
            "def foo(*args, **kwargs)",
            "def foo(value=1, *args, **kwargs)",
            "python",
        )

        assert assessment.category_id == "parameter_reordered"
        assert assessment.breaking is True
        assert any("captured by *args" in item for item in assessment.evidence)

    def test_offset_required_addition_is_binding_shift_not_required_demand(self) -> None:
        assessment = assess_signature_change(
            "def foo(b, /)",
            "def foo(a, b=2)",
            "python",
        )

        assert assessment.rule_id == "DG103"
        assert assessment.category_id == "parameter_reordered"
        assert assessment.breaking is True
        assert any("shifted retained positional binding" in item for item in assessment.evidence)
        assert all("required call demand" not in item for item in assessment.evidence)

    @pytest.mark.parametrize(
        ("old_signature", "new_signature"),
        [
            ("def foo(b=1, /)", "def foo(a, b=2)"),
            ("def foo(b, /)", "def foo(b=2, /, *, a)"),
        ],
    )
    def test_added_required_parameter_keeps_direct_lost_call_witnesses(
        self,
        old_signature: str,
        new_signature: str,
    ) -> None:
        assessment = assess_signature_change(old_signature, new_signature, "python")

        assert assessment.rule_id == "DG102"
        assert assessment.category_id == "required_parameter_added"
        assert assessment.breaking is True

    @pytest.mark.parametrize(
        "new_signature",
        [
            "def foo(required, *, option=1)",
            "def foo(required, /, *, option=1)",
        ],
    )
    def test_required_positional_demand_is_not_offset_by_optional_keyword_only_parameter(
        self,
        new_signature: str,
    ) -> None:
        assessment = assess_signature_change(
            "def foo(*, option)",
            new_signature,
            "python",
        )

        assert assessment.rule_id == "DG102"
        assert assessment.category_id == "required_parameter_added"
        assert assessment.breaking is True
        assert any("required" in item.lower() for item in assessment.evidence)

    def test_required_addition_is_not_masked_by_complementary_variadics(self) -> None:
        assessment = assess_signature_change(
            "def foo(a, b)",
            "def foo(required, *a, **b)",
            "python",
        )

        assert assessment.rule_id == "DG102"
        assert assessment.category_id == "required_parameter_added"
        assert assessment.breaking is True

        def old_function(a: int, b: int) -> tuple[int, int]:
            return a, b

        def new_function(required: int, *a: int, **b: int) -> object:
            return required, a, b

        assert old_function(a=1, b=2) == (1, 2)
        with pytest.raises(TypeError, match="required"):
            new_function(a=1, b=2)

    def test_added_keyword_parameter_does_not_claim_old_kwargs_nonbreaking(self) -> None:
        assessment = assess_signature_change(
            "def foo(args, /, **kwargs)",
            "def foo(required, *args, **kwargs)",
            "python",
        )

        assert assessment.rule_id == "DG103"
        assert assessment.category_id == "parameter_reordered"
        assert assessment.breaking is True
        assert any("**kwargs" in item for item in assessment.evidence)

        def old_function(args: int, /, **kwargs: int) -> object:
            return args, kwargs

        def new_function(required: int, *args: int, **kwargs: int) -> object:
            return required, args, kwargs

        assert old_function(1, required=2) == (1, {"required": 2})
        with pytest.raises(TypeError, match="multiple values"):
            new_function(1, required=2)

    def test_nonpositional_addition_does_not_hide_duplicate_binding_from_reorder(self) -> None:
        assessment = assess_signature_change(
            "def foo(a, /, b)",
            "def foo(b, *a, option=1)",
            "python",
        )

        assert assessment.rule_id == "DG103"
        assert assessment.category_id == "parameter_reordered"
        assert assessment.breaking is True
        assert any("b (1 -> 0)" in item for item in assessment.evidence)

        def old_function(a: int, /, b: int) -> object:
            return a, b

        def new_function(b: int, *a: int, option: int = 1) -> object:
            return b, a, option

        assert old_function(1, b=2) == (1, 2)
        with pytest.raises(TypeError, match="multiple values"):
            new_function(1, b=2)

    def test_addition_does_not_hide_keyword_only_parameter_claiming_old_position(self) -> None:
        assessment = assess_signature_change(
            "def foo(a, /, *, b)",
            "def foo(b, *a, option=1)",
            "python",
        )

        assert assessment.rule_id == "DG110"
        assert assessment.category_id == "parameter_kind_changed"
        assert assessment.breaking is True

        def old_function(a: int, /, *, b: int) -> object:
            return a, b

        def new_function(b: int, *a: int, option: int = 1) -> object:
            return b, a, option

        assert old_function(1, b=2) == (1, 2)
        with pytest.raises(TypeError, match="multiple values"):
            new_function(1, b=2)

    def test_variadic_addition_does_not_hide_semantic_positional_rebinding(self) -> None:
        assessment = assess_signature_change(
            "def foo(a, b)",
            "def foo(b, a, *extra)",
            "python",
        )

        assert assessment.rule_id == "DG103"
        assert assessment.category_id == "parameter_reordered"
        assert assessment.breaking is True
        assert any("a (0 -> 1)" in item for item in assessment.evidence)
        assert any("b (1 -> 0)" in item for item in assessment.evidence)

        def old_function(a: int, b: int) -> tuple[int, int]:
            return a, b

        def new_function(b: int, a: int, *extra: int) -> tuple[int, int]:
            return a, b

        assert old_function(1, 2) == (1, 2)
        assert new_function(1, 2) == (2, 1)

    def test_positional_insertion_without_old_varargs_has_honest_evidence(self) -> None:
        assessment = assess_signature_change(
            "def foo(b)",
            "def foo(a=1, b=2)",
            "python",
        )

        assert assessment.rule_id == "DG103"
        assert assessment.category_id == "parameter_reordered"
        assert assessment.breaking is True
        assert any("shifted retained positional binding" in item for item in assessment.evidence)
        assert all("*args" not in item for item in assessment.evidence)

    def test_equals_inside_annotation_is_not_a_default(self) -> None:
        assessment = assess_signature_change(
            "def foo()",
            'def foo(value: Literal["="])',
            "python",
        )
        assert assessment.category_id == "required_parameter_added"
        assert assessment.breaking is True

    def test_string_and_lambda_default_changes_are_parsed_as_one_parameter(self) -> None:
        string_change = assess_signature_change(
            'def foo(value="a,b")',
            'def foo(value="c,d")',
            "python",
        )
        lambda_change = assess_signature_change(
            "def foo(callback=lambda a, b: a + b)",
            "def foo(callback=lambda a, b: a - b)",
            "python",
        )
        assert string_change.category_id == "default_changed"
        assert string_change.breaking is False
        assert lambda_change.category_id == "default_changed"
        assert lambda_change.breaking is False

    def test_optional_positional_insertion_changes_existing_positions(self) -> None:
        assessment = assess_signature_change(
            "def foo(a=1, b=2)", "def foo(a=1, inserted=0, b=2)", "python"
        )
        assert assessment.category_id == "parameter_reordered"
        assert assessment.breaking is True

    def test_keyword_only_reorder_does_not_break_call_shape(self) -> None:
        assessment = assess_signature_change(
            "def foo(*, first=1, second=2)",
            "def foo(*, second=2, first=1)",
            "python",
        )
        assert assessment.category_id == "parameter_reordered"
        assert assessment.breaking is False

    def test_mixed_reorder_and_rename_does_not_fabricate_paired_syntax(self) -> None:
        assessment = assess_signature_change(
            "def foo(a: int = 1, b: str = 2)",
            "def foo(b: str = 2, c: bytes = 3)",
            "python",
        )

        assert assessment.rule_id == "DG103"
        assert assessment.breaking is True
        assert all("Default changed" not in item for item in assessment.evidence)
        assert all("Annotation syntax" not in item for item in assessment.evidence)

    def test_positional_only_and_variadic_renames_do_not_break_calls(self) -> None:
        positional_only = assess_signature_change("def foo(old, /)", "def foo(new, /)", "python")
        variadic = assess_signature_change(
            "def foo(*old, **old_kw)", "def foo(*new, **new_kw)", "python"
        )
        assert positional_only.category_id == "parameter_renamed"
        assert positional_only.breaking is False
        assert variadic.category_id == "parameter_renamed"
        assert variadic.breaking is False

    @pytest.mark.parametrize(
        ("old_signature", "new_signature"),
        [
            ("def foo(a, b, /)", "def foo(b, c, /)"),
            ("def foo(a, b, c, /)", "def foo(a, c, d, /)"),
            ("def foo(a, b)", "def foo(b, c)"),
            ("def foo(a=1, b=2)", "def foo(b=2, c=3)"),
            ("def foo(a, b, /)", "def foo(b, c)"),
        ],
    )
    def test_overlapping_rename_with_retained_positional_shift_is_reordered(
        self,
        old_signature: str,
        new_signature: str,
    ) -> None:
        assessment = assess_signature_change(old_signature, new_signature, "python")

        assert assessment.rule_id == "DG103"
        assert assessment.category_id == "parameter_reordered"
        assert assessment.breaking is True

    @pytest.mark.parametrize(
        ("old_signature", "new_signature"),
        [
            ("def foo(a, b, /)", "def foo(c, b, /)"),
            ("def foo(a, b, c, /)", "def foo(a, d, c, /)"),
            ("def foo(a, b, /)", "def foo(c, d, /)"),
        ],
    )
    def test_positional_only_rename_with_stable_retained_slots_is_nonbreaking(
        self,
        old_signature: str,
        new_signature: str,
    ) -> None:
        assessment = assess_signature_change(old_signature, new_signature, "python")

        assert assessment.category_id == "parameter_renamed"
        assert assessment.breaking is False

    def test_keyword_only_overlap_is_not_a_positional_reorder(self) -> None:
        assessment = assess_signature_change(
            "def foo(*, a, b)",
            "def foo(*, b, c)",
            "python",
        )

        assert assessment.category_id == "parameter_renamed"
        assert assessment.breaking is True

    @pytest.mark.parametrize(
        ("old_signature", "new_signature"),
        [
            ("def foo(a)", "def foo(b, c)"),
            ("def foo(a, /)", "def foo(*, b)"),
            ("def foo(*, a)", "def foo(*, b, c)"),
            ("def foo(*, a, b)", "def foo(*, c)"),
            ("def foo(a)", "def foo(b, c, **kwargs)"),
        ],
    )
    def test_compound_replacement_with_direct_required_demand_is_breaking(
        self,
        old_signature: str,
        new_signature: str,
    ) -> None:
        assessment = assess_signature_change(old_signature, new_signature, "python")

        assert assessment.rule_id == "DG102"
        assert assessment.category_id == "required_parameter_added"
        assert assessment.breaking is True

    @pytest.mark.parametrize(
        ("old_signature", "new_signature"),
        [
            ("def foo(a, b)", "def foo(c)"),
            ("def foo(a, b, /)", "def foo(c, /)"),
            ("def foo(a)", "def foo(b, /)"),
            ("def foo(a=1)", "def foo(b=1, c=2)"),
            ("def foo(*, a=1)", "def foo(*, b=1, c=2)"),
        ],
    )
    def test_compound_replacement_with_direct_removed_call_mode_is_breaking(
        self,
        old_signature: str,
        new_signature: str,
    ) -> None:
        assessment = assess_signature_change(old_signature, new_signature, "python")

        assert assessment.rule_id == "DG101"
        assert assessment.category_id == "parameter_removed"
        assert assessment.breaking is True

    @pytest.mark.parametrize(
        ("old_signature", "new_signature"),
        [
            ("def foo(a=1, /)", "def foo(b=1, c=2, /)"),
            ("def foo(a, /)", "def foo(b)"),
            ("def foo(a, b)", "def foo(c, *args, **kwargs)"),
            (
                "def foo(a=1)",
                "def foo(b=1, c=2, *args, **kwargs)",
            ),
        ],
    )
    def test_compound_replacement_without_direct_witness_remains_unknown(
        self,
        old_signature: str,
        new_signature: str,
    ) -> None:
        assessment = assess_signature_change(old_signature, new_signature, "python")

        assert assessment.category_id == "parameters_changed"
        assert assessment.breaking is None
        assert assessment.analysis_gaps

    def test_equal_capacity_renames_keep_existing_rename_semantics(self) -> None:
        positional_only = assess_signature_change(
            "def foo(a, b, /)",
            "def foo(c, d, /)",
            "python",
        )
        positional_or_keyword = assess_signature_change(
            "def foo(a, b)",
            "def foo(c, d)",
            "python",
        )

        assert positional_only.category_id == "parameter_renamed"
        assert positional_only.breaking is False
        assert positional_or_keyword.category_id == "parameter_renamed"
        assert positional_or_keyword.breaking is True

    def test_positional_only_rename_does_not_hide_default_removal(self) -> None:
        assessment = assess_signature_change(
            "def foo(old=1, /)",
            "def foo(new, /)",
            "python",
        )
        unchanged_name_assessment = assess_signature_change(
            "def foo(old, other=1, /)",
            "def foo(new, other, /)",
            "python",
        )
        assert assessment.category_id == "default_removed"
        assert assessment.breaking is True
        assert unchanged_name_assessment.category_id == "default_removed"
        assert unchanged_name_assessment.breaking is True

    def test_positional_only_compound_rename_is_not_claimed_compatible(self) -> None:
        assessment = assess_signature_change(
            "def foo(old: int, /)",
            "def foo(new: str, /)",
            "python",
        )
        assert assessment.category_id == "parameters_changed"
        assert assessment.breaking is None
        assert assessment.analysis_gaps

    def test_self_and_cls_are_not_globally_discarded(self) -> None:
        self_removed = assess_signature_change("def foo(self)", "def foo()", "python")
        cls_removed = assess_signature_change("def foo(cls)", "def foo()", "python")
        assert self_removed.category_id == "parameter_removed"
        assert self_removed.breaking is True
        assert cls_removed.category_id == "parameter_removed"
        assert cls_removed.breaking is True

    def test_keyword_callable_rename_breaks_keyword_calls(self) -> None:
        assessment = assess_signature_change("def foo(old)", "def foo(new)", "python")
        assert assessment.category_id == "parameter_renamed"
        assert assessment.breaking is True

    def test_parameter_kind_restrictions_are_breaking(self) -> None:
        positional_only = assess_signature_change("def foo(value)", "def foo(value, /)", "python")
        keyword_only = assess_signature_change("def foo(value)", "def foo(*, value)", "python")
        assert positional_only.category_id == "parameter_kind_changed"
        assert positional_only.breaking is True
        assert keyword_only.category_id == "parameter_kind_changed"
        assert keyword_only.breaking is True

    def test_permissive_parameter_kind_change_cannot_steal_variadic_capture(self) -> None:
        positional_capture = assess_signature_change(
            "def foo(*args, value)",
            "def foo(value, *args)",
            "python",
        )
        keyword_capture = assess_signature_change(
            "def foo(value, /, **kwargs)",
            "def foo(value, **kwargs)",
            "python",
        )

        assert positional_capture.category_id == "parameter_kind_changed"
        assert positional_capture.breaking is True
        assert keyword_capture.category_id == "parameter_kind_changed"
        assert keyword_capture.breaking is True

    @pytest.mark.parametrize(
        ("old_signature", "new_signature"),
        [
            ("def foo(*values)", "def foo(**values)"),
            ("def foo(**values)", "def foo(*values)"),
            ("def foo(*values)", "def foo(values=None)"),
            ("def foo(**values)", "def foo(*, values=None)"),
            ("def foo(value)", "def foo(*value)"),
            ("def foo(value)", "def foo(**value)"),
        ],
    )
    def test_parameter_kind_transition_with_a_lost_call_mode_is_breaking(
        self,
        old_signature: str,
        new_signature: str,
    ) -> None:
        assessment = assess_signature_change(old_signature, new_signature, "python")

        assert assessment.category_id == "parameter_kind_changed"
        assert assessment.breaking is True

    @pytest.mark.parametrize(
        ("old_signature", "new_signature"),
        [
            ("def foo(value, /)", "def foo(*value)"),
            ("def foo(*, value)", "def foo(**value)"),
        ],
    )
    def test_fixed_parameter_to_matching_variadic_is_nonbreaking_superset(
        self,
        old_signature: str,
        new_signature: str,
    ) -> None:
        assessment = assess_signature_change(old_signature, new_signature, "python")

        assert assessment.category_id == "parameter_kind_changed"
        assert assessment.breaking is False

    @pytest.mark.parametrize(
        ("old_signature", "new_signature"),
        [
            ("def foo(value)", "def foo(*value, **kwargs)"),
            ("def foo(value)", "def foo(*args, **value)"),
            ("def foo(first, value)", "def foo(first, *value, **kwargs)"),
            ("def foo(left, right)", "def foo(*left, **right)"),
            ("def foo(value=1)", "def foo(*value, **kwargs)"),
        ],
    )
    def test_complementary_variadics_are_proven_call_shape_supersets(
        self,
        old_signature: str,
        new_signature: str,
    ) -> None:
        assessment = assess_signature_change(old_signature, new_signature, "python")

        assert assessment.category_id == "parameter_kind_changed"
        assert assessment.breaking is False
        assert assessment.analysis_gaps

    def test_complementary_variadics_leave_optional_positional_rebinding_unknown(
        self,
    ) -> None:
        assessment = assess_signature_change(
            "def foo(value, other=1)",
            "def foo(*value, other=1, **kwargs)",
            "python",
        )

        assert assessment.category_id == "parameter_kind_changed"
        assert assessment.breaking is None
        assert assessment.analysis_gaps

    def test_complementary_variadics_do_not_hide_required_positional_loss(self) -> None:
        assessment = assess_signature_change(
            "def foo(value, other)",
            "def foo(*value, other, **kwargs)",
            "python",
        )

        assert assessment.category_id == "parameter_kind_changed"
        assert assessment.breaking is True

    def test_compound_variadic_rename_and_optional_addition_remains_unknown(self) -> None:
        assessment = assess_signature_change(
            "def foo(*args)",
            "def foo(*values, option=None)",
            "python",
        )

        assert assessment.category_id == "parameters_changed"
        assert assessment.breaking is None

    @pytest.mark.parametrize(
        ("old_signature", "new_signature"),
        [
            ("def foo(*args)", "def foo(*values, args=None)"),
            ("def foo(**kwargs)", "def foo(*, kwargs=None, **values)"),
        ],
    )
    def test_replacement_variadic_capture_avoids_false_breaking_certainty(
        self,
        old_signature: str,
        new_signature: str,
    ) -> None:
        assessment = assess_signature_change(old_signature, new_signature, "python")

        assert assessment.category_id == "parameter_kind_changed"
        assert assessment.breaking is None
        assert assessment.analysis_gaps

    @pytest.mark.parametrize(
        ("old_signature", "new_signature"),
        [
            ("def foo(*args)", "def foo(value=None)"),
            ("def foo(**kwargs)", "def foo(*, value=None)"),
            ("def foo(*args)", "def foo(**kwargs)"),
            ("def foo(**kwargs)", "def foo(*args)"),
        ],
    )
    def test_compound_variadic_capture_loss_has_a_direct_breaking_witness(
        self,
        old_signature: str,
        new_signature: str,
    ) -> None:
        assessment = assess_signature_change(old_signature, new_signature, "python")

        assert assessment.category_id == "parameters_changed"
        assert assessment.breaking is True

    def test_permissive_parameter_kind_change_cannot_rebind_old_positionals(self) -> None:
        assessment = assess_signature_change(
            "def foo(first, /, second)",
            "def foo(second, first)",
            "python",
        )

        assert assessment.category_id == "parameter_kind_changed"
        assert assessment.breaking is True

    def test_safe_permissive_parameter_kind_expansions_remain_nonbreaking(self) -> None:
        positional_only = assess_signature_change(
            "def foo(value, /)",
            "def foo(value)",
            "python",
        )
        keyword_only = assess_signature_change(
            "def foo(first, *, value)",
            "def foo(first, value)",
            "python",
        )
        reordered_keyword_only = assess_signature_change(
            "def foo(first, *, left, value)",
            "def foo(first, value, *, left)",
            "python",
        )

        assert positional_only.breaking is False
        assert keyword_only.breaking is False
        assert reordered_keyword_only.breaking is False

    def test_python_decorator_call_is_not_mistaken_for_parameters(self) -> None:
        assessment = assess_signature_change(
            '@route("/x")\ndef f(a)', '@route("/x")\ndef f(a, b)', "python"
        )
        assert assessment.category_id == "required_parameter_added"

    def test_go_receiver_is_not_mistaken_for_parameters(self) -> None:
        assessment = assess_signature_change(
            "func (s *Server) Handle(a int) error",
            "func (s *Server) Handle(a int, b int) error",
            "go",
        )
        assert assessment.category_id == "parameter_added"
        assert assessment.breaking is None

    def test_python_class_bases_are_not_function_parameters(self) -> None:
        assessment = assess_signature_change("class C(A)", "class C(B)", "python")
        assert assessment.category_id == "class_signature_changed"
        assert assessment.breaking is None

    @pytest.mark.parametrize("language", ["typescript", "javascript"])
    @pytest.mark.parametrize(
        ("old_signature", "new_signature"),
        [
            (
                "class Service extends mixin(Base)",
                "class Service extends mixin(Base, Trait)",
            ),
            (
                "class Service extends mixin(Base, Trait)",
                "class Service extends mixin(Base)",
            ),
            ("class Service extends Base", "class Service extends Replacement"),
        ],
    )
    def test_non_python_class_heritage_precedes_parameter_comparison(
        self,
        language: str,
        old_signature: str,
        new_signature: str,
    ) -> None:
        assessment = assess_signature_change(old_signature, new_signature, language)

        assert assessment.category_id == "class_signature_changed"
        assert assessment.breaking is None

    @pytest.mark.parametrize(
        ("language", "declaration"),
        [
            ("typescript", "export class"),
            ("javascript", "export class"),
            ("typescript", "export default class"),
            ("javascript", "export default class"),
            ("typescript", "export abstract class"),
            ("typescript", "export default abstract class"),
        ],
    )
    def test_exported_class_heritage_precedes_parameter_comparison(
        self,
        language: str,
        declaration: str,
    ) -> None:
        assessment = assess_signature_change(
            f"{declaration} Service extends mixin(Base)",
            f"{declaration} Service extends mixin(Base, Trait)",
            language,
        )

        assert assessment.rule_id == "DG110"
        assert assessment.category_id == "class_signature_changed"
        assert assessment.breaking is None

    @pytest.mark.parametrize(
        ("old_signature", "new_signature"),
        [
            ("function service(base: Base)", "function service(base: Base, trait: Trait)"),
            ("service(base: Base)", "service(base: Base, trait: Trait)"),
            (
                "export function service(base: Base)",
                "export function service(base: Base, trait: Trait)",
            ),
        ],
    )
    def test_non_python_callables_still_use_parameter_comparison(
        self,
        old_signature: str,
        new_signature: str,
    ) -> None:
        assessment = assess_signature_change(old_signature, new_signature, "typescript")

        assert assessment.category_id == "parameter_added"

    @pytest.mark.parametrize(
        ("language", "old_signature", "new_signature"),
        [
            (
                "typescript",
                "function contract(value:string,count?:number):OldResult",
                "function contract( value : string , count ? : number ) : NewResult",
            ),
            (
                "go",
                "func Contract(callback func(int,string))(OldResult,error)",
                "func Contract( callback func( int , string ) ) ( NewResult , error )",
            ),
        ],
    )
    def test_non_python_parameter_formatting_does_not_hide_return_type_change(
        self,
        language: str,
        old_signature: str,
        new_signature: str,
    ) -> None:
        comparison = compare_signatures(old_signature, new_signature, language)

        assert comparison.assessment is not None
        assert comparison.assessment.rule_id == "DG108"
        assert comparison.assessment.category_id == "return_type_changed"

    @pytest.mark.parametrize(
        ("language", "old_signature", "new_signature"),
        [
            (
                "typescript",
                "function f(): number",
                "export function f():  number",
            ),
            (
                "javascript",
                "function f()",
                "export function f() /* formatting-only tail comment */",
            ),
            (
                "go",
                "func f() (int,error)",
                "func F() ( int, error )",
            ),
        ],
    )
    def test_non_python_tail_formatting_does_not_claim_return_type_change(
        self,
        language: str,
        old_signature: str,
        new_signature: str,
    ) -> None:
        comparison = compare_signatures(old_signature, new_signature, language)

        assert comparison.assessment is not None
        assert comparison.assessment.rule_id == "DG110"
        assert comparison.assessment.category_id == "signature_changed"

    @pytest.mark.parametrize(
        ("language", "old_signature", "new_signature"),
        [
            (
                "typescript",
                "function f(): number",
                "export function f():  string",
            ),
            (
                "go",
                "func f() (int,error)",
                "func F() ( string, error )",
            ),
        ],
    )
    def test_non_python_real_return_type_change_survives_other_syntax_change(
        self,
        language: str,
        old_signature: str,
        new_signature: str,
    ) -> None:
        comparison = compare_signatures(old_signature, new_signature, language)

        assert comparison.assessment is not None
        assert comparison.assessment.rule_id == "DG108"
        assert comparison.assessment.category_id == "return_type_changed"


class TestCompareSignatures:
    def test_python_formatting_only_signatures_are_structurally_equivalent(self) -> None:
        comparison = compare_signatures(
            "def foo(value:int=1)->list[int]",
            "def foo( value: int = 1 ) -> list[int]",
            "python",
        )
        assert comparison.equivalent is True
        assert comparison.assessment is None

    def test_python_callable_kind_change_is_not_structurally_equivalent(self) -> None:
        comparison = compare_signatures("def foo(value)", "async def foo(value)", "python")
        assert comparison.equivalent is False
        assert comparison.assessment is not None

    @pytest.mark.parametrize(
        ("language", "compact", "spaced"),
        [
            (
                "typescript",
                "function contract<T extends Base>(value:T): number",
                "function contract< T extends Base >( value : T ):number",
            ),
            (
                "javascript",
                "function contract(value={key:1})",
                "function contract( value = { key : 1 } )",
            ),
            (
                "go",
                "func Contract[T ~string](value T)(Result,error)",
                "func Contract[ T ~string ]( value T ) ( Result, error )",
            ),
        ],
    )
    def test_non_python_formatting_only_signatures_are_lexically_equivalent(
        self,
        language: str,
        compact: str,
        spaced: str,
    ) -> None:
        comparison = compare_signatures(compact, spaced, language)

        assert comparison.equivalent is True
        assert comparison.assessment is None

    def test_non_python_default_expression_spacing_is_equivalent(self) -> None:
        comparison = compare_signatures(
            "function contract(value=left+right):number",
            "function contract( value = left + right ) : number",
            "typescript",
        )

        assert comparison.equivalent is True

    @pytest.mark.parametrize("separator", ["\n", "/*\n*/"])
    @pytest.mark.parametrize(
        "source",
        [
            "function f(cb=function(){return<SEP>value}):void",
            "function f(cb=function(){throw<SEP>value}):void",
            "function f(cb=function(){loop:while(x){break<SEP>loop}}):void",
            "function f(cb=function(){loop:while(x){continue<SEP>loop}}):void",
            "function f(cb=function*(){yield<SEP>value}):void",
            "function f(cb=async<SEP>function(){}):void",
            "function f(cb=async<SEP>x=>x):void",
            "function f(cb=async<SEP>(x:T):U=>x):void",
            "function f(cb=async<SEP><T>(x:T):T=>x):void",
            "function f(value={async<SEP>method(){}}):void",
            "function f(cb=function(){value<SEP>++}):void",
            "function f(cb=(value)<SEP>=>value):void",
            "function f(cb=(value:T):Promise<T><SEP>=>value):void",
        ],
    )
    def test_javascript_restricted_production_line_terminators_remain_visible(
        self,
        separator: str,
        source: str,
    ) -> None:
        compact = source.replace("<SEP>", " ")
        terminated = source.replace("<SEP>", separator)

        comparison = compare_signatures(compact, terminated, "typescript")

        assert comparison.equivalent is False
        assert comparison.assessment is not None

    @pytest.mark.parametrize("separator", ["\r", "\u2028", "\u2029"])
    def test_all_javascript_line_terminator_code_points_remain_visible(
        self,
        separator: str,
    ) -> None:
        compact = "function f(cb=function(){return value}):void"
        terminated = f"function f(cb=function(){{return{separator}value}}):void"

        assert compare_signatures(compact, terminated, "javascript").equivalent is False

    @pytest.mark.parametrize(
        ("compact", "wrapped"),
        [
            (
                "function f(value=left+right):number",
                "function\nf(\nvalue=left\n+\nright\n)\n:\nnumber",
            ),
            (
                "function f(value=left+right):number",
                "function f(value=left/*\n*/+right):number",
            ),
            (
                "function f(value=obj.return/total/2):number",
                "function f(value=obj.return\n/ total / 2):number",
            ),
            (
                "function f(value={return:1}):number",
                "function f(value={return\n:1}):number",
            ),
            (
                "function f(value=async/total/2):number",
                "function f(value=async\n/ total / 2):number",
            ),
            (
                "function f(value=++count):number",
                "function f(value=\n++count):number",
            ),
            (
                "function f(cb=function(){return}):void",
                "function f(cb=function(){return\n}):void",
            ),
        ],
    )
    def test_ordinary_javascript_line_wrapping_remains_equivalent(
        self,
        compact: str,
        wrapped: str,
    ) -> None:
        assert compare_signatures(compact, wrapped, "typescript").equivalent is True

    @pytest.mark.parametrize(
        ("language", "old_signature", "new_signature"),
        [
            (
                "typescript",
                "function f(value:string/* old ), /[)]/, `x` */,next=1):number",
                "function f(value:string/* new ], /[},]/, `y` */,next=1):number",
            ),
            (
                "javascript",
                "function f(value=1,// old ), /[)]/, `x`\nnext=2)",
                "function f(value=1,// new ], /[},]/, `y`\nnext=2)",
            ),
            (
                "go",
                "func F(value int/* old ), /[)]/, `x` */,next int) error",
                "func F(value int/* new ], /[},]/, `y` */,next int) error",
            ),
            (
                "typescript",
                "function f(value:string,next=1):number",
                "function f(value:string/* added ), /x/ */,next=1):number",
            ),
        ],
    )
    def test_non_python_comment_only_edits_are_equivalent(
        self,
        language: str,
        old_signature: str,
        new_signature: str,
    ) -> None:
        assert compare_signatures(old_signature, new_signature, language).equivalent is True

    @pytest.mark.parametrize(
        ("old_signature", "new_signature"),
        [
            (
                "function f(value=/a b//* old comment */,next=1):boolean",
                "function f(value=/a  b//* new comment */,next=1):boolean",
            ),
            (
                "function f(value=left+right/* old comment */,next=1):number",
                "function f(value=left-right/* new comment */,next=1):number",
            ),
            (
                'function f(value="a b"/* old comment */,next=1):string',
                'function f(value="ab"/* new comment */,next=1):string',
            ),
        ],
    )
    def test_comments_do_not_hide_literal_or_operator_changes(
        self,
        old_signature: str,
        new_signature: str,
    ) -> None:
        assert compare_signatures(old_signature, new_signature, "typescript").equivalent is False

    @pytest.mark.parametrize(
        ("language", "compact", "spaced"),
        [
            (
                "typescript",
                "function contract(value=flag&&/a b/.test(text)):boolean",
                "function contract( value = flag && /a b/.test( text ) ) : boolean",
            ),
            (
                "javascript",
                "function contract(value=!/a b/.test(text))",
                "function contract( value = ! /a b/.test( text ) )",
            ),
            (
                "javascript",
                "function contract(value=total/count)",
                "function contract( value = total / count )",
            ),
        ],
    )
    def test_javascript_regex_and_division_contexts_normalize_external_spacing(
        self,
        language: str,
        compact: str,
        spaced: str,
    ) -> None:
        assert compare_signatures(compact, spaced, language).equivalent is True

    @pytest.mark.parametrize(
        ("old_signature", "new_signature"),
        [
            (
                "function contract(value = flag && /a b/.test(text)): boolean",
                "function contract(value = flag && /a  b/.test(text)): boolean",
            ),
            (
                "function contract(value = !/a b/.test(text)): boolean",
                "function contract(value = !/a  b/.test(text)): boolean",
            ),
            (
                "function contract(value = total / count): number",
                "function contract(value = total * count): number",
            ),
        ],
    )
    def test_javascript_regex_contents_and_division_operator_changes_remain_visible(
        self,
        old_signature: str,
        new_signature: str,
    ) -> None:
        assert compare_signatures(old_signature, new_signature, "typescript").equivalent is False

    @pytest.mark.parametrize(
        "identifier",
        ["async", "as", "await", "infer", "keyof", "let", "readonly", "satisfies", "yield"],
    )
    def test_contextual_identifiers_before_division_remain_identifiers(
        self,
        identifier: str,
    ) -> None:
        compact = f"function f(value={identifier}/total/2,next=1):number"
        spaced = f"function f( value = {identifier} / total / 2 , next = 1 ) : number"

        assert compare_signatures(compact, spaced, "typescript").equivalent is True

    @pytest.mark.parametrize(
        ("compact_expression", "spaced_expression"),
        [
            ("obj.else/total/2", "obj . else / total / 2"),
            ("of/total/2", "of / total / 2"),
            ("makePair<string>/left/right", "makePair< string > / left / right"),
            ("makePair<custom>/left/right", "makePair < custom > / left / right"),
            ("makePair<custom>/left/g", "makePair < custom > / left / g"),
            (
                "makePair<Box<string>>/left/right",
                "makePair < Box < string > > / left / right",
            ),
            ("value!/left/right", "value ! / left / right"),
            ("value as const/left/right", "value as const / left / right"),
            ("{}/left/right", "{ } / left / right"),
            ("function(){}/left/right", "function ( ) { } / left / right"),
            ("async x=>({value:x})/left/right", "async x => ( { value : x } ) / left / right"),
        ],
    )
    def test_expression_close_goals_preserve_division(
        self,
        compact_expression: str,
        spaced_expression: str,
    ) -> None:
        compact = f"function f(value={compact_expression},next=1):number"
        spaced = f"function f( value = {spaced_expression} , next = 1 ) : number"

        assert compare_signatures(compact, spaced, "typescript").equivalent is True

    @pytest.mark.parametrize(
        "source",
        [
            "cb=function(){for(const x of /a b/){}}",
            "cb=function(){if(x){}else /a b/.test(x)}",
            "cb=function(){if(x){} /a b/.test(x)}",
            "cb=function(){if(x) /a b/.test(x)}",
            "cb=function(){{work();} /a b/.test(x)}",
            "cb=function(){label: {} /a b/.test(x)}",
            "cb=function(){try{}catch{} /a b/.test(x)}",
            "function declared() {} /a b/.test(x)",
            "cb=function(){typeof /a b/}",
            "cb=async function(){await /a b/}",
            "cb=function*(){yield /a b/}",
            "cb=function(){return /a b/}",
            "cb=async ()=>{await /a b/}",
            "cb=async x=>{await /a b/}",
            "cb=async x=>await /a b/.test(x)",
            "cb=async (x)=>await /a b/.test(x)",
            "cb=async x=>fn(await /a b/,await /[)]/)",
            "cb={async m(){return await /a b/.test(x)}}",
            "cb={*m(){return yield /a b/}}",
            "cb=a < b > /a b/.test(x)",
        ],
    )
    def test_regex_contents_remain_visible_after_statement_and_prefix_goals(
        self,
        source: str,
    ) -> None:
        changed = source.replace("/a b/", "/a  b/")

        assert compare_signatures(source, changed, "typescript").equivalent is False

    @pytest.mark.parametrize(
        ("old_signature", "new_signature"),
        [
            ("cb=makePair<string>/a b/", "cb=makePair<string>/a  b/"),
            ("cb=Box<T>/a{1}/g", "cb=Box<T>/a{2}/g"),
            ("cb=a<b>/a b/", "cb=a<b>/a  b/"),
        ],
    )
    def test_type_looking_comparisons_do_not_hide_regex_literal_changes(
        self,
        old_signature: str,
        new_signature: str,
    ) -> None:
        assert compare_signatures(old_signature, new_signature, "typescript").equivalent is False

    def test_statement_regex_contexts_still_ignore_external_spacing(self) -> None:
        compact = "function f(cb=function(){{work();}/a b/.test(x)},next=1):number"
        spaced = (
            "function f( cb = function ( ) { { work ( ) ; } /a b/.test ( x ) } , "
            "next = 1 ) : number"
        )

        assert compare_signatures(compact, spaced, "typescript").equivalent is True

    def test_concise_async_context_ends_at_the_parameter_comma(self) -> None:
        compact = "function f(cb=async x=>await /r/,await=await/total/2):void"
        spaced = "function f( cb = async x => await /r/ , await = await / total / 2 ) : void"

        assert compare_signatures(compact, spaced, "typescript").equivalent is True

    def test_object_property_comma_does_not_create_a_method_context(self) -> None:
        compact = "function f(value={x:call(a,b),async:async/total/2},next=1):void"
        spaced = (
            "function f( value = { x : call( a , b ) , async : async / total / 2 } , "
            "next = 1 ) : void"
        )

        assert compare_signatures(compact, spaced, "typescript").equivalent is True

        nested_compact = "function f(value={x:call(first,async/total/2),next:1},other=1):void"
        nested_spaced = (
            "function f( value = { x : call( first , async / total / 2 ) , next : 1 } , "
            "other = 1 ) : void"
        )
        assert compare_signatures(nested_compact, nested_spaced, "typescript").equivalent is True

    def test_template_scanner_uses_statement_and_operand_goals(self) -> None:
        templates = [
            "`${function(){if(x){} /[}}`]/.test(x)}}`",
            "`${()=>{{}/[}`]/.test(text)}}`",
            "`${{a:1}/total/2}`",
        ]

        for template in templates:
            compact = f"function f(value={template},next=1):number"
            spaced = f"function f( value = {template} , next = 1 ) : number"
            assert compare_signatures(compact, spaced, "typescript").equivalent is True

    @pytest.mark.parametrize(
        "template",
        [
            "`${a / b}`",
            "`${flag && /a b/.test(text)}`",
            '`outer ${{text: "}", ratio: a / b, matcher: /a{1,2} b/, nested: `inner ${value}`}}`',
        ],
    )
    def test_template_interpolations_end_before_following_signature_whitespace(
        self,
        template: str,
    ) -> None:
        compact = f"function contract(value={template},other:string):number"
        spaced = f"function contract( value = {template} , other : string ) : number"

        assert compare_signatures(compact, spaced, "typescript").equivalent is True

    @pytest.mark.parametrize(
        ("old_signature", "new_signature"),
        [
            (
                'function contract(value = "a b"): string',
                'function contract(value = "ab"): string',
            ),
            (
                "function contract(value = `a b`): string",
                "function contract(value = `ab`): string",
            ),
            (
                "function contract(value = `outer ${`a b`}`): string",
                "function contract(value = `outer ${`ab`}`): string",
            ),
            (
                "function contract(value = `${flag && /a b/.test(text)}`): string",
                "function contract(value = `${flag && /a  b/.test(text)}`): string",
            ),
            (
                "function contract(value = left + right): number",
                "function contract(value = left - right): number",
            ),
            (
                "function contract(cb: (value: T) => U): void",
                "function contract(cb: (value: T) <= U): void",
            ),
        ],
    )
    def test_non_python_literal_and_operator_changes_remain_visible(
        self,
        old_signature: str,
        new_signature: str,
    ) -> None:
        comparison = compare_signatures(old_signature, new_signature, "typescript")

        assert comparison.equivalent is False
        assert comparison.assessment is not None

    def test_pep695_formatting_is_equivalent_on_supported_python_versions(self) -> None:
        comparison = compare_signatures(
            "def contract[T: (str, tuple[int, bytes])](value:T=1)->T",
            "def contract[ T : ( str , tuple [ int , bytes ] ) ]( value : T = 1 ) -> T",
            "python",
        )

        assert comparison.equivalent is True
        assert comparison.assessment is None

    def test_decorated_async_pep695_call_shape_is_parsed_without_host_grammar(self) -> None:
        old = '@route("/value")\nasync def contract[*Ts, **P](value: tuple[*Ts] = ())'
        default_removed = compare_signatures(
            old,
            '@route("/value")\nasync def contract[*Ts, **P](value: tuple[*Ts])',
            "python",
        )
        required_added = compare_signatures(
            old,
            '@route("/value")\nasync def contract[*Ts, **P]'
            "(value: tuple[*Ts] = (), *, required: int)",
            "python",
        )

        assert default_removed.assessment is not None
        assert default_removed.assessment.category_id == "default_removed"
        assert default_removed.assessment.breaking is True
        assert required_added.assessment is not None
        assert required_added.assessment.category_id == "required_parameter_added"
        assert required_added.assessment.breaking is True

    def test_pep695_type_parameter_changes_remain_visible(self) -> None:
        comparison = compare_signatures(
            "def contract[T: (str, tuple[int, bytes])](value: T) -> T",
            "def contract[T: (str, tuple[int, bytearray])](value: T) -> T",
            "python",
        )

        assert comparison.equivalent is False
        assert comparison.assessment is not None
        assert comparison.assessment.category_id == "type_parameters_changed"
        assert comparison.assessment.breaking is None

    def test_type_parameter_default_tokens_are_compared_without_host_ast_support(self) -> None:
        equivalent = compare_signatures(
            "def contract[T: str = str](value: T)",
            "def contract[ T : str = str ]( value : T )",
            "python",
        )
        changed = compare_signatures(
            "def contract[T: str = str](value: T)",
            "def contract[T: str = bytes](value: T)",
            "python",
        )

        assert equivalent.equivalent is True
        assert changed.assessment is not None
        assert changed.assessment.category_id == "type_parameters_changed"

    def test_pep695_class_formatting_is_structurally_equivalent(self) -> None:
        comparison = compare_signatures(
            "@decorator\nclass Contract[T: tuple[str, list[int]]](Base)",
            "@decorator\nclass Contract[ T : tuple [ str , list [ int ] ] ]( Base )",
            "python",
        )

        assert comparison.equivalent is True


class TestIsDefaultValueChange:
    def test_changed_existing_default(self) -> None:
        assert is_default_value_change("def f(a=1)", "def f(a=2)") is True

    def test_default_removal_is_a_distinct_rule(self) -> None:
        assert is_default_value_change("def f(a=1)", "def f(a)") is False

    def test_parameter_shape_change_is_not_default_only(self) -> None:
        assert is_default_value_change("def f(a=1)", "def f(a=1, b=2)") is False
        assert is_default_value_change("def f(a: int=1)", "def f(a: str=2)") is False
