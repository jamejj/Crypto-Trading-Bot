-- Append-only account entry latch. No automatic resolution/clearance policy in P03.
CREATE TABLE execution_incidents (
    account_id text NOT NULL,
    intent_id text NOT NULL,
    kind text NOT NULL CHECK (kind='TERMINAL_PARTIAL_FOK'),
    received_at timestamptz,
    evidence jsonb NOT NULL,
    PRIMARY KEY (account_id,intent_id,kind),
    FOREIGN KEY (account_id,intent_id) REFERENCES execution_intents(account_id,intent_id)
);
-- Existing positive terminal partial evidence also blocks an upgraded offline DB.
-- NULL receipt time means legacy timing is unknown; do not invent a new timestamp.
INSERT INTO execution_incidents
SELECT account_id,intent_id,'TERMINAL_PARTIAL_FOK',NULL,projection
FROM execution_orders
WHERE (
    projection->>'status' IN ('FILLED','CANCELED','REJECTED','EXPIRED')
    AND (projection->'filled'->'amount'->>'$decimal')::numeric > 0
    AND (projection->'filled'->'amount'->>'$decimal')::numeric
        < (projection->'target'->'amount'->>'$decimal')::numeric
) OR EXISTS (
    SELECT 1 FROM jsonb_array_elements(projection->'observations') AS obs
    WHERE obs->>'status' IN ('FILLED','CANCELED','REJECTED','EXPIRED')
      AND (obs->'cumulative_quantity'->'amount'->>'$decimal')::numeric > 0
      AND (obs->'cumulative_quantity'->'amount'->>'$decimal')::numeric
          < (projection->'target'->'amount'->>'$decimal')::numeric
);
CREATE TRIGGER incidents_immutable BEFORE UPDATE OR DELETE ON execution_incidents
    FOR EACH ROW EXECUTE FUNCTION execution_immutable();
CREATE TRIGGER incidents_no_truncate BEFORE TRUNCATE ON execution_incidents
    FOR EACH STATEMENT EXECUTE FUNCTION execution_immutable();
