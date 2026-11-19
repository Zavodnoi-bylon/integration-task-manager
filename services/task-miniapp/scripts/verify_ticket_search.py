"""Read-only production smoke check. Prints counts, never titles or identities."""
import asyncio
import os
from psycopg.conninfo import make_conninfo
from dest_miniapp.canonical import PostgresMiniAppGateway
from dest_miniapp.gateway import TicketQuery
from dest_miniapp.models import Actor
from dest_miniapp.postgres import PostgresDatabase


async def main():
    db = PostgresDatabase(make_conninfo(os.environ["DATABASE_URL"],
        options="-c default_transaction_read_only=on"))
    await db.open()
    try:
        for profile in ("kalisto", "mephisto"):
            gateway = PostgresMiniAppGateway(db, profile_key=profile,
                profile_name=profile, advertiser_catalog=None, postback_service=None)
            async with db.pool.connection() as conn:
                users = await (await conn.execute(
                    "SELECT u.telegram_user_id, m.role FROM ticketing.memberships m "
                    "JOIN ticketing.telegram_users u USING(user_id) "
                    "WHERE m.profile_key=%s AND m.active", (profile,))).fetchall()
            pages_checked = 0
            for user in users:
                actor = Actor(user['telegram_user_id'], 'Smoke', user['role'], True)
                scope = 'team' if actor.is_staff or actor.is_supervisor else 'mine'
                first = await gateway.list_tickets(profile_key=profile, actor=actor,
                    query=TicketQuery(scope, None, False, 1, 50))
                second = await gateway.list_tickets(profile_key=profile, actor=actor,
                    query=TicketQuery(scope, None, False, 2, 50))
                assert not ({x.ticket_id for x in first.items} & {x.ticket_id for x in second.items})
                for sample in first.items[:1] + second.items[:1]:
                    found = await gateway.list_tickets(profile_key=profile, actor=actor,
                        query=TicketQuery(scope, None, False, 1, 100, sample.title))
                    assert found.total > 0
                    assert all(sample.title.lower() in x.title.lower() for x in found.items)
                pages_checked += 2
            print(f'{profile}: read-only search/pagination OK, pages={pages_checked}')
    finally:
        await db.close()


asyncio.run(main())
