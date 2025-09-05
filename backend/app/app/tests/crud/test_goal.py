import pytest
from datetime import date, datetime
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud
from app.schemas.goal import GoalCreate, GoalUpdate
from app.tests.utils.user import create_random_user
from app.tests.utils.utils import random_lower_string, random_float


@pytest.mark.asyncio
async def test_create_goal(db: AsyncSession) -> None:
    name = random_lower_string()
    description = random_lower_string()
    target_amount = random_float()
    target_date = date.today()
    user = await create_random_user(db)
    goal_in = GoalCreate(
        name=name, 
        description=description, 
        target_amount=target_amount,
        target_date=target_date
    )
    goal = await crud.goal.create_with_owner(db=db, obj_in=goal_in, owner_id=user.id)
    assert goal.name == name
    assert goal.description == description
    assert goal.target_amount == target_amount
    assert goal.current_amount == 0.0
    assert goal.target_date == target_date
    assert goal.owner_id == user.id
    assert goal.is_completed is False


@pytest.mark.asyncio
async def test_get_goal(db: AsyncSession) -> None:
    name = random_lower_string()
    target_amount = random_float()
    user = await create_random_user(db)
    goal_in = GoalCreate(name=name, target_amount=target_amount)
    goal = await crud.goal.create_with_owner(db=db, obj_in=goal_in, owner_id=user.id)
    stored_goal = await crud.goal.get(db=db, id=goal.id)
    assert stored_goal
    assert goal.id == stored_goal.id
    assert goal.name == stored_goal.name
    assert goal.owner_id == stored_goal.owner_id


@pytest.mark.asyncio
async def test_update_goal(db: AsyncSession) -> None:
    name = random_lower_string()
    target_amount = random_float()
    user = await create_random_user(db)
    goal_in = GoalCreate(name=name, target_amount=target_amount)
    goal = await crud.goal.create_with_owner(db=db, obj_in=goal_in, owner_id=user.id)
    
    new_name = random_lower_string()
    goal_update = GoalUpdate(name=new_name)
    updated_goal = await crud.goal.update(db=db, db_obj=goal, obj_in=goal_update)
    assert goal.id == updated_goal.id
    assert goal.owner_id == updated_goal.owner_id
    assert updated_goal.name == new_name


@pytest.mark.asyncio
async def test_delete_goal(db: AsyncSession) -> None:
    name = random_lower_string()
    target_amount = random_float()
    user = await create_random_user(db)
    goal_in = GoalCreate(name=name, target_amount=target_amount)
    goal = await crud.goal.create_with_owner(db=db, obj_in=goal_in, owner_id=user.id)
    goal2 = await crud.goal.remove(db=db, id=goal.id)
    goal3 = await crud.goal.get(db=db, id=goal.id)
    assert goal3 is None
    assert goal2.id == goal.id
    assert goal2.name == name


@pytest.mark.asyncio
async def test_update_goal_progress(db: AsyncSession) -> None:
    name = random_lower_string()
    target_amount = 1000.0
    user = await create_random_user(db)
    goal_in = GoalCreate(name=name, target_amount=target_amount)
    goal = await crud.goal.create_with_owner(db=db, obj_in=goal_in, owner_id=user.id)
    
    # Add progress
    contribution_amount = 250.0
    updated_goal = await crud.goal.update_progress(
        db=db, goal_id=goal.id, owner_id=user.id, amount=contribution_amount
    )
    assert updated_goal.current_amount == contribution_amount
    assert updated_goal.is_completed is False
    
    # Complete the goal
    remaining_amount = 750.0
    completed_goal = await crud.goal.update_progress(
        db=db, goal_id=goal.id, owner_id=user.id, amount=remaining_amount
    )
    assert completed_goal.current_amount == target_amount
    assert completed_goal.is_completed is True


@pytest.mark.asyncio
async def test_get_goals_by_owner(db: AsyncSession) -> None:
    user = await create_random_user(db)
    goal1_in = GoalCreate(name=random_lower_string(), target_amount=random_float())
    goal2_in = GoalCreate(name=random_lower_string(), target_amount=random_float())
    
    goal1 = await crud.goal.create_with_owner(db=db, obj_in=goal1_in, owner_id=user.id)
    goal2 = await crud.goal.create_with_owner(db=db, obj_in=goal2_in, owner_id=user.id)
    
    stored_goals = await crud.goal.get_multi_by_owner(db=db, owner_id=user.id)
    assert len(stored_goals) >= 2
    
    goal_ids = [g.id for g in stored_goals]
    assert goal1.id in goal_ids
    assert goal2.id in goal_ids