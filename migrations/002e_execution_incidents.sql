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
-- A terminal projection with fewer local trades may only mean missing records.
-- Never invent partial-FOK evidence from the local filled quantity.
WHERE EXISTS (
    SELECT 1 FROM jsonb_array_elements(projection->'observations') AS obs
    WHERE obs->>'status' IN ('FILLED','CANCELED','REJECTED','EXPIRED')
      AND obs->>'terminal' = 'true'
      AND (obs->'cumulative_quantity'->'amount'->>'$decimal')::numeric > 0
      AND (obs->'cumulative_quantity'->'amount'->>'$decimal')::numeric
          < (projection->'target'->'amount'->>'$decimal')::numeric
);
CREATE TRIGGER incidents_immutable BEFORE UPDATE OR DELETE ON execution_incidents
    FOR EACH ROW EXECUTE FUNCTION execution_immutable();
CREATE TRIGGER incidents_no_truncate BEFORE TRUNCATE ON execution_incidents
    FOR EACH STATEMENT EXECUTE FUNCTION execution_immutable();
