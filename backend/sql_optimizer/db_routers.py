"""
Database router to route seed database operations to the 'seed_db' database.
"""
from django.conf import settings


class SeedDBRouter:
    """
    A router to control database operations for the seed database.
    """

    def db_for_read(self, model, **hints):
        """Point read operations for seed models to 'seed_db'."""
        if model._meta.app_label == 'schema_engine' and hasattr(model, 'USE_SEED_DB') and model.USE_SEED_DB:
            return 'seed_db'
        return None

    def db_for_write(self, model, **hints):
        """Point write operations for seed models to 'seed_db'."""
        if model._meta.app_label == 'schema_engine' and hasattr(model, 'USE_SEED_DB') and model.USE_SEED_DB:
            return 'seed_db'
        return None

    def allow_relation(self, obj1, obj2, **hints):
        """Allow relations if both models use seed_db or neither does."""
        use_seed_1 = obj1._meta.app_label == 'schema_engine' and hasattr(obj1, 'USE_SEED_DB') and obj1.USE_SEED_DB
        use_seed_2 = obj2._meta.app_label == 'schema_engine' and hasattr(obj2, 'USE_SEED_DB') and obj2.USE_SEED_DB
        return use_seed_1 == use_seed_2

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        """Ensure seed models only migrate to seed_db."""
        if app_label == 'schema_engine':
            model = hints.get('model')
            if model and hasattr(model, 'USE_SEED_DB') and model.USE_SEED_DB:
                return db == 'seed_db'
            return db == 'default'
        return None