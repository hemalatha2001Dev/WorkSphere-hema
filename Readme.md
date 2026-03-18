mysql -h 127.0.0.1 -u root -p



uvicorn app.main:app --reload


find . -type d -name "__pycache__" -exec rm -r {} +

uvicorn app.main:app --reload --host 0.0.0.0 --port 8001 --reload






# Keep POST as is - for adding messages
@router.post("/chat", response_model=ChatResponseSchema)
def add_chat_message(
    chat: ChatMessageSchema,
    db: Session = Depends(get_mysql_session),
    current_user: dict = Depends(get_current_user),
):
    """Add chat message to project"""
    try:
        chat_doc = {
            "project_id": chat.project_id,
            "sender_name": chat.sender_name,
            "sender_id": chat.sender_id,
            "message": chat.message,
            "timestamp": datetime.utcnow(),
        }

        mongo_client = get_mongo_client()
        if mongo_client:
            mongo_db = mongo_client[settings.MONGO_DB_NAME]
            mongo_db["project_chats"].insert_one(chat_doc)

        existing_project = (
            db.query(Project).filter(Project.id == chat.project_id).first()
        )
        if existing_project:
            # Create chat data for MySQL - exclude timestamp and any MongoDB _id
            mysql_chat_data = {
                "project_id": chat_doc["project_id"],
                "sender_name": chat_doc["sender_name"],
                "sender_id": chat_doc["sender_id"],
                "message": chat_doc["message"],
            }
            new_chat = ProjectChat(**mysql_chat_data)
            db.add(new_chat)
            db.commit()
            db.refresh(new_chat)
            return ChatResponseSchema(
                id=new_chat.id,
                project_id=new_chat.project_id,
                sender_name=new_chat.sender_name,
                sender_id=new_chat.sender_id,
                message=new_chat.message,
                timestamp=new_chat.timestamp,
            )

        return ChatResponseSchema(
            id=0,
            project_id=chat_doc["project_id"],
            sender_name=chat_doc["sender_name"],
            sender_id=chat_doc["sender_id"],
            message=chat_doc["message"],
            timestamp=chat_doc["timestamp"],
        )

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=500, detail=f"Error adding chat message: {str(e)}"
        )


# Change GET to use a different path to avoid conflicts
@router.get("/chat/messages", response_model=List[ChatResponseSchema])
def get_project_chats(
    project_id: int = Query(..., description="Project ID to get chat messages for"),
    db: Session = Depends(get_mysql_session),
    current_user: dict = Depends(get_current_user),
):
    """
    Get all chat messages for a project.
    Example: GET /api/v1/projects/chat/messages?project_id=18
    """
    chats_list = []

    try:
        # Try MongoDB first
        mongo_client = get_mongo_client()
        if mongo_client:
            mongo_db = mongo_client[settings.MONGO_DB_NAME]
            docs = list(
                mongo_db["project_chats"]
                .find({"project_id": project_id})
                .sort("timestamp", 1)
            )
            for d in docs:
                chats_list.append(
                    ChatResponseSchema(
                        id=str(d.get("_id")),  # Keep as string for MongoDB ObjectId
                        project_id=d["project_id"],
                        sender_name=d.get("sender_name", "Unknown"),
                        sender_id=d.get("sender_id", "0"),
                        message=d.get("message", ""),
                        timestamp=d.get("timestamp", datetime.utcnow()),
                    )
                )

        # Fallback to MySQL if MongoDB has no data
        if not chats_list:
            chats = (
                db.query(ProjectChat)
                .filter(ProjectChat.project_id == project_id)
                .order_by(ProjectChat.timestamp.asc())
                .all()
            )
            for c in chats:
                chats_list.append(
                    ChatResponseSchema(
                        id=c.id,  # Integer from MySQL
                        project_id=c.project_id,
                        sender_name=c.sender_name,
                        sender_id=c.sender_id,
                        message=c.message,
                        timestamp=c.timestamp,
                    )
                )

        return chats_list

    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error fetching chat messages: {str(e)}"
        )


1.instead of the normal api service of both @router.post("/chat") ; @router.get("/chat/messages",); convert into real time chat functionality using sockets in both get and post api
2.then,both sides(sender and reciever) will open the chat interface connect the socket 
3.then,sneder and reciever able to get the real time chat msgs instatly 





4.if sender sends the message then reciever is not active on the chat interface then immediatly check within the api that socket status is offline 
then,after verifying socket satus is offline then send the msg to respective user's email id this needs to be an immediate action so api need is
5.create one api connected to check whether connected socket status is online or offline so,based on this modify the post and get api's 
6.use this existing email service for this 