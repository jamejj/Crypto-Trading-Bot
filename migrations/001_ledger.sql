-- P02 accounting schema. Apply in one transaction to an explicitly selected schema.
CREATE TABLE ledger_accounts (
    account_id text PRIMARY KEY,
    quote text NOT NULL,
    version bigint NOT NULL CHECK (version >= 0),
    snapshot jsonb NOT NULL
);
CREATE TABLE ledger_operations (
    account_id text NOT NULL REFERENCES ledger_accounts(account_id),
    version bigint NOT NULL CHECK (version > 0),
    method text NOT NULL,
    payload jsonb NOT NULL,
    PRIMARY KEY (account_id, version)
);
CREATE TABLE ledger_postings (
    account_id text NOT NULL,
    version bigint NOT NULL,
    line integer NOT NULL CHECK (line >= 0),
    book_account text NOT NULL,
    currency text NOT NULL CHECK (currency <> ''),
    amount numeric NOT NULL CHECK (amount::text NOT IN ('NaN', 'Infinity', '-Infinity')),
    PRIMARY KEY (account_id, version, line),
    FOREIGN KEY (account_id, version) REFERENCES ledger_operations(account_id, version)
);
CREATE FUNCTION ledger_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'ledger history is append-only'; END $$;
CREATE TRIGGER operations_immutable BEFORE UPDATE OR DELETE ON ledger_operations
    FOR EACH ROW EXECUTE FUNCTION ledger_immutable();
CREATE TRIGGER postings_immutable BEFORE UPDATE OR DELETE ON ledger_postings
    FOR EACH ROW EXECUTE FUNCTION ledger_immutable();
CREATE TRIGGER operations_no_truncate BEFORE TRUNCATE ON ledger_operations
    FOR EACH STATEMENT EXECUTE FUNCTION ledger_immutable();
CREATE TRIGGER postings_no_truncate BEFORE TRUNCATE ON ledger_postings
    FOR EACH STATEMENT EXECUTE FUNCTION ledger_immutable();
CREATE FUNCTION ledger_balanced() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF EXISTS (SELECT currency FROM ledger_postings
               WHERE account_id = NEW.account_id AND version = NEW.version
               GROUP BY currency HAVING sum(amount) <> 0) THEN
        RAISE EXCEPTION 'unbalanced currency postings';
    END IF;
    RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER balanced_postings AFTER INSERT ON ledger_postings
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION ledger_balanced();
