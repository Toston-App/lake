from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud, models, schemas
from app.api import deps

router = APIRouter()


@router.get("/getAll", response_model=list[schemas.Goal])
async def read_goals(
    db: AsyncSession = Depends(deps.async_get_db),
    skip: int = 0,
    limit: int = 100,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Retrieve goals.
    """
    if crud.user.is_superuser(current_user):
        goals = await crud.goal.get_multi(db, skip=skip, limit=limit)
    else:
        goals = await crud.goal.get_multi_by_owner(
            db=db, owner_id=current_user.id, skip=skip, limit=limit
        )
    return goals


@router.get("/active", response_model=list[schemas.Goal])
async def read_active_goals(
    db: AsyncSession = Depends(deps.async_get_db),
    skip: int = 0,
    limit: int = 100,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Retrieve active (not completed) goals.
    """
    goals = await crud.goal.get_active_goals_by_owner(
        db=db, owner_id=current_user.id, skip=skip, limit=limit
    )
    return goals


@router.get("/completed", response_model=list[schemas.Goal])
async def read_completed_goals(
    db: AsyncSession = Depends(deps.async_get_db),
    skip: int = 0,
    limit: int = 100,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Retrieve completed goals.
    """
    goals = await crud.goal.get_completed_goals_by_owner(
        db=db, owner_id=current_user.id, skip=skip, limit=limit
    )
    return goals


@router.post("", response_model=schemas.Goal)
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
    goal = await crud.goal.get(db=db, id=id)
    if not goal:
        raise HTTPException(status_code=404, detail="Goal not found")
    if not crud.user.is_superuser(current_user) and (
        goal.owner_id != current_user.id
    ):
        raise HTTPException(status_code=400, detail="Not enough permissions")
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
    goal = await read_goal(db=db, id=id, current_user=current_user)

    # Validate account ownership if account_id is provided
    if goal_in.account_id:
        account = await crud.account.get(db=db, id=goal_in.account_id)
        if not account or account.owner_id != current_user.id:
            goal_in.account_id = goal.account_id

    # Convert target_date string to date object if provided
    if goal_in.target_date and isinstance(goal_in.target_date, str):
        try:
            goal_in.target_date = datetime.strptime(goal_in.target_date, "%Y-%m-%d").date()
        except:
            goal_in.target_date = goal.target_date

    goal_in.updated_at = datetime.now(timezone.utc)

    updated_goal = await crud.goal.update(db=db, db_obj=goal, obj_in=goal_in)
    return updated_goal


@router.post("/{id}/contribute", response_model=schemas.Goal)
async def contribute_to_goal(
    *,
    db: AsyncSession = Depends(deps.async_get_db),
    id: int,
    amount: float,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Add a contribution amount to a goal's progress.
    """
    if amount <= 0:
        raise HTTPException(status_code=400, detail="Amount must be positive")
    
    goal = await crud.goal.update_progress(
        db=db, goal_id=id, owner_id=current_user.id, amount=amount
    )
    
    if not goal:
        raise HTTPException(status_code=404, detail="Goal not found or not enough permissions")
    
    return goal


@router.put("/{id}/complete", response_model=schemas.Goal)
async def mark_goal_completed(
    *,
    db: AsyncSession = Depends(deps.async_get_db),
    id: int,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Mark a goal as completed.
    """
    goal = await crud.goal.mark_completed(
        db=db, goal_id=id, owner_id=current_user.id
    )
    
    if not goal:
        raise HTTPException(status_code=404, detail="Goal not found or not enough permissions")
    
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
    goal = await read_goal(db=db, id=id, current_user=current_user)
    
    await crud.goal.remove(db=db, id=id)
    
    return schemas.DeletionResponse(message=f"Goal {id} deleted")


@router.delete("/bulk/{ids}", response_model=schemas.BulkDeletionResponse)
async def delete_goals_bulk(
    *,
    db: AsyncSession = Depends(deps.async_get_db),
    ids: str,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Delete multiple goals at once.
    Format: /bulk/1,2,3
    """
    try:
        id_list = [int(id.strip()) for id in ids.split(",")]
    except ValueError:
        raise HTTPException(
            status_code=400, detail="Invalid ID format. Use comma-separated integers"
        )

    # Verify permissions for all goals
    valid_ids = []
    for id in id_list:
        goal = await crud.goal.get(db=db, id=id)
        if not goal:
            continue
        if not crud.user.is_superuser(current_user) and (
            goal.owner_id != current_user.id
        ):
            raise HTTPException(
                status_code=400, detail=f"Not enough permissions for goal {id}"
            )
        valid_ids.append(goal.id)

    if not valid_ids:
        raise HTTPException(status_code=404, detail="No valid goals found")

    removed_goals = await crud.goal.remove_multi(db=db, ids=valid_ids)

    return schemas.BulkDeletionResponse(
        message=f"Deleted {len(removed_goals)} goals",
        deleted_ids=[g.id for g in removed_goals],
    )