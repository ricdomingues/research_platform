from virtual_orders.api.errors import to_api_error
from virtual_orders.ledger import errors
from virtual_orders.ledger.errors import LedgerIntegrityError


def test_integrity_errors_are_409_on_commands_and_500_on_reads():
    exc = LedgerIntegrityError(errors.STORED_HASH_MISMATCH, "stored hash differs", order_id=None)
    command, read = to_api_error(exc, "POST"), to_api_error(exc, "GET")
    assert (command.status_code, command.code, command.reason) == (409, "INTEGRITY_ERROR", errors.STORED_HASH_MISMATCH)
    assert (read.status_code, read.code, read.reason) == (500, "INTEGRITY_ERROR", errors.STORED_HASH_MISMATCH)


def test_unmapped_exceptions_become_internal_error_without_the_message():
    error = to_api_error(RuntimeError("secret-ish detail"), "POST")
    assert (error.status_code, error.code, error.reason, error.detail) == (
        500, "INTERNAL_ERROR", None, {"type": "RuntimeError"})
