from typing import Any, List
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud, models, schemas
from app.api import deps
from app.models.goal import GoalStatus

router = APIRouter()


@router.get("/", response_model=List[schemas.Goal])
async def read_goals(
    db: AsyncSession = Depends(deps.async_get_db),
    skip: int = 0,
    limit: int = 100,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Retrieve goals for the current user.
    """
    goals = await crud.goal.get_multi_by_owner(
        db=db, owner_id=current_user.id, skip=skip, limit=limit
    )
    return goals


@router.get("/status/{status}", response_model=List[schemas.Goal])
async def read_goals_by_status(
    *,
    db: AsyncSession = Depends(deps.async_get_db),
    status: GoalStatus,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Retrieve goals by status for the current user.
    """
    goals = await crud.goal.get_goals_by_status(
        db=db, owner_id=current_user.id, status=status
    )
    return goals


@router.get("/overdue", response_model=List[schemas.Goal])
async def read_overdue_goals(
    db: AsyncSession = Depends(deps.async_get_db),
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Retrieve overdue goals and update their status.
    """
    goals = await crud.goal.get_overdue_goals(db=db, owner_id=current_user.id)
    return goals


@router.get("/stats", response_model=dict)
async def read_goal_stats(
    db: AsyncSession = Depends(deps.async_get_db),
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Retrieve goal progress statistics.
    """
    stats = await crud.goal.get_goal_progress_stats(db=db, owner_id=current_user.id)
    return stats


@router.post("/", response_model=schemas.Goal)
async def create_goal(
    *,
    db: AsyncSession = Depends(deps.async_get_db),
    goal_in: schemas.GoalCreate,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Create new goal.
    """
    goal = await crud.goal.create_with_owner(
        db=db, obj_in=goal_in, owner_id=current_user.id
    )
    return goal


@router.get("/{id}", response_model=schemas.Goal)
async def read_goal(
    *,
    db: AsyncSession = Depends(deps.async_get_db),
    id: int,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Get goal by ID.
    """
    goal = await crud.goal.get_by_owner_and_id(
        db=db, owner_id=current_user.id, goal_id=id
    )
    if not goal:
        raise HTTPException(status_code=404, detail="Goal not found")
    return goal


@router.put("/{id}", response_model=schemas.Goal)
async def update_goal(
    *,
    db: AsyncSession = Depends(deps.async_get_db),
    id: int,
    goal_in: schemas.GoalUpdate,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Update a goal.
    """
    goal = await crud.goal.get_by_owner_and_id(
        db=db, owner_id=current_user.id, goal_id=id
    )
    if not goal:
        raise HTTPException(status_code=404, detail="Goal not found")

    goal = await crud.goal.update_with_owner(
        db=db, db_obj=goal, obj_in=goal_in, owner_id=current_user.id
    )
    return goal


@router.delete("/{id}", response_model=schemas.DeletionResponse)
async def delete_goal(
    *,
    db: AsyncSession = Depends(deps.async_get_db),
    id: int,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Delete a goal.
    """
    goal = await crud.goal.remove_with_owner(
        db=db, goal_id=id, owner_id=current_user.id
    )
    if not goal:
        raise HTTPException(status_code=404, detail="Goal not found")
    
    return schemas.DeletionResponse(message=f"Goal '{goal.name}' deleted successfully")