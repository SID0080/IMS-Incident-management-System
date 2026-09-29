from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # App
    APP_NAME: str = "Adani IMS"
    VERSION:  str = "2.0.0"
    DEBUG:    bool = False

    # Database
    DATABASE_URL: str

    # JWT
    SECRET_KEY:                  str
    ALGORITHM:                   str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60

    # ── Company access code ───────────────────────────────
    # All WORKER and MANAGER registrations/logins must supply this.
    # ADMIN accounts are exempt (system accounts, not field staff).
    COMPANY_ACCESS_CODE: str = "ADANI-IMS-2024"

    # ML
    ML_MODEL_PATH:          str   = "app/ml/models/categorizer.joblib"
    TF_MODEL_PATH:          str   = "app/ml/models/image_classifier"
    ML_CONFIDENCE_THRESHOLD: float = 0.6

    # SLA (hours)
    SLA_LOW_RESPONSE_HRS:      float = 24
    SLA_MEDIUM_RESPONSE_HRS:   float = 8
    SLA_HIGH_RESPONSE_HRS:     float = 2
    SLA_CRITICAL_RESPONSE_HRS: float = 0.5

    # Email / SMTP
    SMTP_HOST:        str = "smtp.gmail.com"
    SMTP_PORT:        int = 587
    SMTP_USER:        str = ""
    SMTP_PASSWORD:    str = ""
    ALERT_EMAIL_FROM: str = "ims-alerts@adani.com"

    class Config:
        env_file = ".env"


settings = Settings()
