from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel, Field
from typing import Optional, List, Dict
from passlib.context import CryptContext
from datetime import datetime, timedelta
import jwt
import uuid

# ==== FastAPI App Initialization ====

app = FastAPI(
    title="Tic Tac Toe Backend",
    description="API for Tic Tac Toe Game with Authentication, Match Management, and Game Logic.",
    version="1.0.0",
    openapi_tags=[
        {"name": "auth", "description": "User registration and authentication"},
        {"name": "game", "description": "Game management and tic tac toe logic"},
        {"name": "history", "description": "User match history"},
    ],
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==== Configuration & Utils ====

# Use a SECRET_KEY for JWT signing. In production, use an environment variable.
SECRET_KEY = "dev-secret-key-please-change"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/token")

# In-memory "database"
users_db: Dict[str, Dict] = dict()
games_db: Dict[str, Dict] = dict()
matches_db: Dict[str, List[Dict]] = dict()

def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)

def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

def decode_access_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except jwt.PyJWTError:
        return {}

def get_current_user(token: str = Depends(oauth2_scheme)):
    payload = decode_access_token(token)
    username: str = payload.get("sub")
    if username is None or username not in users_db:
        raise HTTPException(status_code=401, detail="Could not validate credentials")
    return users_db[username]

# ==== Pydantic Models ====

class UserRegisterRequest(BaseModel):
    username: str = Field(..., description="Unique username (alphanumeric)")
    password: str = Field(..., min_length=4, description="Password string (min 4 chars)")

class UserRegisterResponse(BaseModel):
    message: str

class TokenResponse(BaseModel):
    access_token: str
    token_type: str

class GameStartRequest(BaseModel):
    opponent: str = Field(..., description="The username of the opponent to play with")

class GameState(BaseModel):
    game_id: str
    player_x: str
    player_o: str
    board: List[List[Optional[str]]]  # "X", "O" or None
    current_turn: str
    status: str  # "ongoing", "finished"
    winner: Optional[str]
    is_draw: bool

class MakeMoveRequest(BaseModel):
    game_id: str
    row: int = Field(..., ge=0, le=2)
    col: int = Field(..., ge=0, le=2)

class MoveResponse(BaseModel):
    board: List[List[Optional[str]]]
    current_turn: str
    status: str
    winner: Optional[str]
    is_draw: bool

class MatchRecord(BaseModel):
    game_id: str
    player_x: str
    player_o: str
    outcome: str # "win", "lose", "draw"
    winner: Optional[str]
    started_at: datetime
    finished_at: datetime

class MatchHistoryResponse(BaseModel):
    matches: List[MatchRecord]

# ==== User Authentication Endpoints ====


# PUBLIC_INTERFACE
@app.post("/auth/register", response_model=UserRegisterResponse, tags=["auth"], summary="Register new user")
def register_user(payload: UserRegisterRequest):
    """
    Register a new user in the Tic Tac Toe system.

    - **username**: Unique username;
    - **password**: Password for the account.

    Returns message on success or 400 on username conflict.
    """
    username = payload.username.lower()
    if username in users_db:
        raise HTTPException(status_code=400, detail="Username already exists")
    users_db[username] = {
        "username": username,
        "hashed_password": get_password_hash(payload.password),
    }
    matches_db[username] = []
    return {"message": "User registered successfully"}


# PUBLIC_INTERFACE
@app.post("/auth/token", response_model=TokenResponse, tags=["auth"], summary="Login to obtain JWT token")
def login_for_access_token(form_data: OAuth2PasswordRequestForm = Depends()):
    """
    Authenticate user and get a JWT access token.

    - **username**: Username
    - **password**: Password

    Returns access token or 401 for invalid credentials.
    """
    username = form_data.username.lower()
    user = users_db.get(username)
    if not user or not verify_password(form_data.password, user["hashed_password"]):
        raise HTTPException(status_code=401, detail="Incorrect username or password")
    access_token = create_access_token(data={"sub": username})
    return {"access_token": access_token, "token_type": "bearer"}

# ==== Game Logic Functions ====

def initialize_board() -> List[List[Optional[str]]]:
    return [[None, None, None] for _ in range(3)]

def check_winner(board: List[List[Optional[str]]]) -> Optional[str]:
    # Rows and columns
    for i in range(3):
        if board[i][0] == board[i][1] == board[i][2] and board[i][0]:
            return board[i][0]
        if board[0][i] == board[1][i] == board[2][i] and board[0][i]:
            return board[0][i]
    # Diagonals
    if board[0][0] == board[1][1] == board[2][2] and board[0][0]:
        return board[0][0]
    if board[0][2] == board[1][1] == board[2][0] and board[0][2]:
        return board[0][2]
    return None

def is_draw(board: List[List[Optional[str]]]) -> bool:
    for row in board:
        if None in row:
            return False
    if not check_winner(board):
        return True
    return False

def user_in_game(game: dict, username: str) -> bool:
    return username in (game["player_x"], game["player_o"])

# ==== Game Endpoints ====

# PUBLIC_INTERFACE
@app.post("/game/start", response_model=GameState, tags=["game"], summary="Start a new game")
def start_game(payload: GameStartRequest, user: dict = Depends(get_current_user)):
    """
    Start a new Tic Tac Toe game between current user and specified opponent.

    - **opponent**: Username of the player to challenge

    Returns new game state, or errors if opponent is invalid or self-challenged.
    """
    user1 = user["username"]
    user2 = payload.opponent.lower()
    if user1 == user2:
        raise HTTPException(status_code=400, detail="Cannot play against yourself")
    if user2 not in users_db:
        raise HTTPException(status_code=404, detail="Opponent not found")
    # X always starts (challenger is X)
    game_id = str(uuid.uuid4())
    now = datetime.utcnow()
    game = {
        "game_id": game_id,
        "player_x": user1,
        "player_o": user2,
        "board": initialize_board(),
        "current_turn": user1,  # X starts
        "status": "ongoing",
        "winner": None,
        "is_draw": False,
        "started_at": now,
        "finished_at": None,
    }
    games_db[game_id] = game
    return GameState(
        game_id=game["game_id"],
        player_x=game["player_x"],
        player_o=game["player_o"],
        board=game["board"],
        current_turn=game["current_turn"],
        status=game["status"],
        winner=game["winner"],
        is_draw=game["is_draw"],
    )

# PUBLIC_INTERFACE
@app.get("/game/{game_id}", response_model=GameState, tags=["game"], summary="Get current game state")
def get_game(game_id: str, user: dict = Depends(get_current_user)):
    """
    Retrieve the current state of a specified game.

    - **game_id**: Game identifier

    Only a participant can view the game.
    """
    game = games_db.get(game_id)
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")
    if not user_in_game(game, user["username"]):
        raise HTTPException(status_code=403, detail="Not a participant in this game")
    return GameState(
        game_id=game["game_id"],
        player_x=game["player_x"],
        player_o=game["player_o"],
        board=game["board"],
        current_turn=game["current_turn"],
        status=game["status"],
        winner=game["winner"],
        is_draw=game["is_draw"],
    )

# PUBLIC_INTERFACE
@app.post("/game/move", response_model=MoveResponse, tags=["game"], summary="Make a move")
def make_move(payload: MakeMoveRequest, user: dict = Depends(get_current_user)):
    """
    Make a move in a Tic Tac Toe game.

    - **game_id**: The game Id
    - **row, col**: Location to make the move (0-2)

    Returns updated board and status. Validates turn, location, and updates history when finished.
    """
    game = games_db.get(payload.game_id)
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")
    username = user["username"]
    if not user_in_game(game, username):
        raise HTTPException(status_code=403, detail="Not a participant in this game")
    if game["status"] != "ongoing":
        raise HTTPException(status_code=400, detail="Game already finished")

    marker = "X" if game["player_x"] == username else "O" if game["player_o"] == username else None
    if not marker:
        raise HTTPException(status_code=400, detail="Invalid player")
    if game["current_turn"] != username:
        raise HTTPException(status_code=400, detail="Not your turn")
    row, col = payload.row, payload.col
    if game["board"][row][col] is not None:
        raise HTTPException(status_code=400, detail="Cell already taken")

    # Place marker and determine game outcome
    game["board"][row][col] = marker

    winner_marker = check_winner(game["board"])
    if winner_marker:
        game["status"] = "finished"
        winner = game["player_x"] if winner_marker == "X" else game["player_o"]
        game["winner"] = winner
        game["finished_at"] = datetime.utcnow()
        game["is_draw"] = False
        # Save to match history for both players
        for u, me_marker in [(game["player_x"], "X"), (game["player_o"], "O")]:
            outcome = "win" if ((me_marker == winner_marker and u == winner)) else "lose"
            matches_db[u].append({
                "game_id": game["game_id"],
                "player_x": game["player_x"],
                "player_o": game["player_o"],
                "outcome": outcome,
                "winner": winner,
                "started_at": game["started_at"],
                "finished_at": game["finished_at"],
            })
    elif is_draw(game["board"]):
        game["status"] = "finished"
        game["winner"] = None
        game["is_draw"] = True
        game["finished_at"] = datetime.utcnow()
        # Save to match history for both players
        for u in [game["player_x"], game["player_o"]]:
            matches_db[u].append({
                "game_id": game["game_id"],
                "player_x": game["player_x"],
                "player_o": game["player_o"],
                "outcome": "draw",
                "winner": None,
                "started_at": game["started_at"],
                "finished_at": game["finished_at"],
            })
    else:
        # Switch turn
        game["current_turn"] = (
            game["player_o"] if game["current_turn"] == game["player_x"] else game["player_x"]
        )

    return MoveResponse(
        board=game["board"],
        current_turn=game["current_turn"],
        status=game["status"],
        winner=game["winner"],
        is_draw=game["is_draw"],
    )

# ==== Match History ====

# PUBLIC_INTERFACE
@app.get("/history", response_model=MatchHistoryResponse, tags=["history"], summary="View match history")
def view_match_history(user: dict = Depends(get_current_user)):
    """
    Get the user's match history.

    Returns a list of matches played, with outcomes, in reverse chronological order.
    """
    username = user["username"]
    history = matches_db.get(username, [])
    matches = [MatchRecord(**rec) for rec in sorted(history, key=lambda r: r["finished_at"], reverse=True)]
    return {"matches": matches}

# ==== Health Check Route (kept for deployment check) ====

@app.get("/", tags=["health"], summary="API health check")
def health_check():
    """Health check endpoint."""
    return {"message": "Healthy"}

