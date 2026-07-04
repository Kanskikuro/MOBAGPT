from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import Champion, ChampionAbility, ChampionStats, ChampionTag, Patch


def test_champion_round_trip(session: Session) -> None:
    patch = Patch(version="14.14.1")
    session.add(patch)
    session.flush()

    champion = Champion(
        champion_id=103,
        riot_key="Ahri",
        display_name="Ahri",
        normalized_name="ahri",
        title="the Nine-Tailed Fox",
        patch_id=patch.id,
    )
    session.add(champion)
    session.flush()

    session.add(
        ChampionStats(
            champion_id=103,
            hp=590,
            hp_per_level=92,
            mp=418,
            mp_per_level=25,
            move_speed=330,
            armor=21,
            armor_per_level=4.7,
            spell_block=30,
            spell_block_per_level=1.3,
            attack_range=550,
            hp_regen=2.5,
            hp_regen_per_level=0.6,
            mp_regen=8,
            mp_regen_per_level=0.8,
            crit=0,
            crit_per_level=0,
            attack_damage=53,
            attack_damage_per_level=3,
            attack_speed_per_level=2,
            attack_speed=0.668,
            attack_rating=3,
            defense_rating=4,
            magic_rating=8,
            difficulty_rating=5,
        )
    )
    session.add(ChampionTag(champion_id=103, tag="Mage", source="data_dragon"))
    session.add(
        ChampionAbility(
            champion_id=103,
            slot="Q",
            ability_id="OrbOfDeception",
            name="Orb of Deception",
            description="Ahri sends out an orb.",
            cooldown=[7, 6.5, 6, 5.5, 5],
            cost=[65, 65, 65, 65, 65],
            ability_range=[880],
            raw_data={"id": "OrbOfDeception"},
        )
    )
    session.commit()

    fetched = session.execute(
        select(Champion).where(Champion.champion_id == 103)
    ).scalar_one()

    assert fetched.display_name == "Ahri"
    assert fetched.stats.hp == 590
    assert fetched.stats.attack_rating == 3
    assert [tag.tag for tag in fetched.tags] == ["Mage"]
    assert fetched.abilities[0].cooldown == [7, 6.5, 6, 5.5, 5]
    assert fetched.patch_id == patch.id
