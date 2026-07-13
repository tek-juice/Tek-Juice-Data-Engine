"""
DATA ENGINE — Seed: Default Tenants and Admin User
Run with: python -m database.seeds.seed_tenants
"""

import asyncio
import uuid

from sqlalchemy import text
from configs.database import AsyncSessionLocal, init_db
from configs.security import hash_password


async def seed_tenants() -> None:
    await init_db()

    async with AsyncSessionLocal() as session:
        # Check if already seeded
        result = await session.execute(text("SELECT COUNT(*) FROM tenants"))
        count = result.scalar()
        if count and count > 0:
            print("Tenants already seeded — skipping.")
            return

        default_tenant_id = str(uuid.uuid4())
        admin_user_id = str(uuid.uuid4())

        # Create default tenant
        await session.execute(
            text("""
                INSERT INTO tenants (id, name, slug, tier, is_active, metadata)
                VALUES (:id, :name, :slug, :tier, true, :metadata)
            """),
            {
                "id": default_tenant_id,
                "name": "Tek Juice AI",
                "slug": "tek-juice-ai",
                "tier": "enterprise",
                "metadata": '{"plan": "enterprise", "max_documents": 100000}',
            },
        )

        # Create admin user
        await session.execute(
            text("""
                INSERT INTO users (id, tenant_id, email, hashed_password, full_name, role)
                VALUES (:id, :tenant_id, :email, :hashed_password, :full_name, 'admin')
            """),
            {
                "id": admin_user_id,
                "tenant_id": default_tenant_id,
                "email": "admin@tekjuice.ai",
                "hashed_password": hash_password("ChangeMe123!"),
                "full_name": "System Administrator",
            },
        )

        await session.commit()
        print(f"✓ Default tenant created: {default_tenant_id}")
        print(f"✓ Admin user created: admin@tekjuice.ai")
        print("  → Change the default password immediately in production!")


if __name__ == "__main__":
    asyncio.run(seed_tenants())
