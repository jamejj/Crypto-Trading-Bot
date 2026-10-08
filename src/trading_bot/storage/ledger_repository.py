"""Account-serialized PostgreSQL transactions use the same deterministic offline ledger."""

import json

from trading_bot.accounting.ledger import Ledger

from .transactions import decode_arguments, encode_arguments, transaction


class LedgerRepository:
    """Connection factory must return a fresh connection to a migrated, selected schema.

    No global/default database or network connection is created. Reads replay immutable
    journal and compare postings. Writes additionally reject projection/audit disagreement.
    """

    def __init__(self, connection_factory, account_id, quote):
        self.connect = connection_factory
        self.account_id, self.quote = account_id, quote

    def _lock(self, conn):
        initial = Ledger(self.account_id, self.quote).snapshot.to_json()
        conn.execute(
            """INSERT INTO ledger_accounts VALUES (%s, %s, 0, %s::jsonb)
                        ON CONFLICT (account_id) DO NOTHING""",
            (self.account_id, self.quote, initial),
        )
        row = conn.execute(
            """SELECT quote, version, snapshot FROM ledger_accounts
                              WHERE account_id = %s FOR UPDATE""",
            (self.account_id,),
        ).fetchone()
        if row[0] != self.quote:
            raise ValueError("account quote identity conflict")
        return row

    def _replay(self, conn):
        operations = []
        versions = []
        for version, method, payload in conn.execute(
            """SELECT version, method, payload
                FROM ledger_operations WHERE account_id = %s ORDER BY version""",
            (self.account_id,),
        ):
            operations.append((method, decode_arguments(method, payload)))
            versions.append(version)
        if versions != list(range(1, len(versions) + 1)):
            raise ValueError("journal version gap")
        book = Ledger.replay(self.account_id, self.quote, operations)
        persisted = conn.execute(
            """SELECT book_account, currency, amount
            FROM ledger_postings WHERE account_id = %s ORDER BY version, line""",
            (self.account_id,),
        ).fetchall()
        expected = [(p.account, p.value.currency, p.value.amount) for p in book.entries]
        if persisted != expected:
            raise ValueError("ledger postings disagree with immutable journal")
        return book

    @property
    def snapshot(self):
        with transaction(self.connect) as conn:
            row = self._lock(conn)
            book = self._replay(conn)
            if row[1] != book.version or json.loads(book.snapshot.to_json()) != row[2]:
                raise ValueError("projection/audit disagreement requires explicit rebuild")
            return book.snapshot

    def replay(self):
        with transaction(self.connect) as conn:
            self._lock(conn)
            return self._replay(conn)

    def rebuild(self):
        with transaction(self.connect) as conn:
            self._lock(conn)
            book = self._replay(conn)
            self._project(conn, book)
            return book.snapshot

    def _project(self, conn, book):
        conn.execute(
            """UPDATE ledger_accounts SET version = %s, snapshot = %s::jsonb
                        WHERE account_id = %s""",
            (book.version, book.snapshot.to_json(), self.account_id),
        )

    def _execute(self, method, *arguments):
        with transaction(self.connect) as conn:
            row = self._lock(conn)
            book = self._replay(conn)
            if row[1] != book.version or json.loads(book.snapshot.to_json()) != row[2]:
                raise ValueError("projection/audit disagreement requires explicit rebuild")
            old_version, entry_count = book.version, len(book.entries)
            result = getattr(book, method)(*arguments)
            if book.version == old_version:
                return result
            journal_method, args = book.operations[-1]
            conn.execute(
                """INSERT INTO ledger_operations VALUES (%s, %s, %s, %s::jsonb)""",
                (self.account_id, book.version, journal_method, encode_arguments(args)),
            )
            for line, posting in enumerate(book.entries[entry_count:]):
                conn.execute(
                    """INSERT INTO ledger_postings VALUES (%s, %s, %s, %s, %s, %s)""",
                    (
                        self.account_id,
                        book.version,
                        line,
                        posting.account,
                        posting.value.currency,
                        posting.value.amount,
                    ),
                )
            self._project(conn, book)
            return result

    def post_cashflow(self, source_id, value, quote_value=None, evidence=None):
        return self._execute("post_cashflow", source_id, value, quote_value, evidence)

    def post_fill(self, fill):
        return self._execute("post_fill", fill)

    def post_fee_correction(self, fee):
        return self._execute("post_fee_correction", fee)

    def reserve(self, reservation, expected_version):
        return self._execute("reserve", reservation, expected_version)

    def release(self, reservation_id, terminal_evidence):
        return self._execute("release", reservation_id, terminal_evidence)

    def value(self, snapshot, market, cost_estimate):
        return self._execute("value", snapshot, market, cost_estimate)

    def mark_dust(self, instrument, minimum):
        return self._execute("mark_dust", instrument, minimum)
