CREATE TABLE IF NOT EXISTS ocr_audit_logs (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ocr_id        TEXT NOT NULL,
    call_id       TEXT NOT NULL,
    tenant_id     TEXT NOT NULL,
    doc_type      TEXT NOT NULL DEFAULT 'general',
    status        TEXT NOT NULL,          -- extracted | failed
    char_count    INTEGER NOT NULL DEFAULT 0,
    fail_reason   TEXT,
    extracted_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ocr_audit_logs_call_id   ON ocr_audit_logs(call_id);
CREATE INDEX IF NOT EXISTS idx_ocr_audit_logs_tenant_id ON ocr_audit_logs(tenant_id);
CREATE INDEX IF NOT EXISTS idx_ocr_audit_logs_ocr_id    ON ocr_audit_logs(ocr_id);
