     1|"""Mnemosyne — Local L3 vector memory provider for Hermes.
     2|
     3|Redis (L1 cache) + pgvector (L2 semantic search). Zero cloud dependencies.
     4|All data stays local on your machine.
     5|
     6|Requires:
     7|  - Redis on 127.0.0.1:6379 (system service)
     8|  - PostgreSQL with pgvector extension (user-owned cluster on port 5433)
     9|  - Python packages: redis, psycopg2-binary, numpy
    10|  - An embedding endpoint (OpenRouter or local Ollama)
    11|
    12|Config via $HERMES_HOME/mnemosyne.json or environment variables:
    13|  MNEMOSYNE_EMBEDDING_PROVIDER  — "openrouter" (default) or "ollama"
    14|  MNEMOSYNE_EMBEDDING_MODEL     — model name (default: openai/text-embedding-3-small)
    15|  MNEMOSYNE_PG_HOST             — PostgreSQL host (default: 127.0.0.1)
    16|  MNEMOSYNE_PG_PORT             — PostgreSQL port (default: 5433)
    17|  MNEMOSYNE_PG_DB               — Database name (default: hermes_vectors)
    18|  MNEMOSYNE_PG_USER             — Database user (default: hermes)
    19|  MNEMOSYNE_PG_PASSWORD         — Database password (default: your_db_password)
    20|  MNEMOSYNE_REDIS_URL           — Redis URL (default: redis://127.0.0.1:***@property
   188|    def name(self) -> str:
   189|        return "mnemosyne"
   190|
   191|    def is_available(self) -> bool:
   192|        """Check if Redis and PostgreSQL are reachable."""
   193|        try:
   194|            cfg = _load_config()
   195|            import redis as redis_lib
   196|            r = redis_lib.from_url(cfg["redis_url"])
   197|            r.ping()
   198|            r.close()
   199|        except Exception:
   200|            return False
   201|        try:
   202|            cfg = _load_config()
   203|            import psycopg2
   204|            conn = psycopg2.connect(
   205|                host=cfg["pg_host"], port=cfg["pg_port"],
   206|                database=cfg["pg_db"], user=cfg["pg_user"],
   207|                password=cfg["pg_password"],
   208|                connect_timeout=3,
   209|            )
   210|            conn.close()
   211|        except Exception:
   212|            return False
   213|        return True
   214|
   215|    def get_config_schema(self):
   216|        return [
   217|            {"key": "embedding_provider", "description": "Embedding provider: openrouter or ollama",
   218|             "default": "openrouter", "choices": ["openrouter", "ollama"]},
   219|            {"key": "embedding_model", "description": "Embedding model name",
   220|             "default": "openai/text-embedding-3-small"},
   221|            {"key": "pg_host", "description": "PostgreSQL host", "default": "127.0.0.1"},
   222|            {"key": "pg_port", "description": "PostgreSQL port", "default": "5433"},
   223|            {"key": "pg_db", "description": "Database name", "default": "hermes_vectors"},
   224|            {"key": "pg_user", "description": "Database user", "default": "hermes"},
   225|            {"key": "pg_password", "description": "Database password",
   226|             "secret": True, "env_var": "MNEMOSYNE_PG_PASSWORD"},
   227|            {"key": "redis_url", "description": "Redis URL", "default": "redis://127.0.0.1:6379"},
   228|        ]
   229|
   230|    def save_config(self, values, hermes_home):
   231|        from pathlib import Path
   232|        config_path = Path(hermes_home) / "mnemosyne.json"
   233|        existing = {}
   234|        if config_path.exists():
   235|            try:
   236|                existing = json.loads(config_path.read_text())
   237|            except Exception:
   238|                pass
   239|        existing.update(values)
   240|        config_path.write_text(json.dumps(existing, indent=2))
   241|
   242|    # -- Core lifecycle -------------------------------------------------------
   243|
   244|    def initialize(self, session_id: str, **kwargs) -> None:
   245|        self._config = _load_config()
   246|        self._session_id = session_id
   247|        self._pg_config = {
   248|            "host": self._config["pg_host"],
   249|            "port": self._config["pg_port"],
   250|            "database": self._config["pg_db"],
   251|            "user": self._config["pg_user"],
   252|            "password": self._config["pg_password"],
   253|        }
   254|
   255|        import redis as redis_lib
   256|        self._redis = redis_lib.from_url(self._config["redis_url"])
   257|
   258|        # Ensure tables exist
   259|        self._ensure_tables()
   260|
   261|        logger.info("Mnemosyne initialized (session=%s, pg=%s:%s)",
   262|                     session_id, self._pg_config["host"], self._pg_config["port"])
   263|
   264|    def _ensure_tables(self):
   265|        """Create tables if they don't exist."""
   266|        import psycopg2
   267|        conn = psycopg2.connect(**self._pg_config)
   268|        try:
   269|            cur = conn.cursor()
   270|            cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
   271|            cur.execute("""
   272|                CREATE TABLE IF NOT EXISTS conversation_memory (
   273|                    id SERIAL PRIMARY KEY,
   274|                    source TEXT,
   275|                    content TEXT,
   276|                    embedding vector(1536),
   277|                    metadata JSONB DEFAULT '{}',
   278|                    created_at TIMESTAMPTZ DEFAULT NOW()
   279|                );
   280|            """)
   281|            cur.execute("""
   282|                CREATE TABLE IF NOT EXISTS knowledge_base (
   283|                    id SERIAL PRIMARY KEY,
   284|                    source TEXT,
   285|                    content TEXT,
   286|                    embedding vector(1536),
   287|                    metadata JSONB DEFAULT '{}',
   288|                    created_at TIMESTAMPTZ DEFAULT NOW()
   289|                );
   290|            """)
   291|            conn.commit()
   292|        finally:
   293|            conn.close()
   294|
   295|    def _get_conn(self):
   296|        import psycopg2
   297|        return psycopg2.connect(**self._pg_config)
   298|
   299|    # -- System prompt --------------------------------------------------------
   300|
   301|    def system_prompt_block(self) -> str:
   302|        return (
   303|            "# Mnemosyne Memory (Local L3)\n"
   304|            "Active. Redis cache + pgvector semantic search.\n"
   305|            "Use mnemosyne_search for semantic recall, mnemosyne_recall for "
   306|            "conversation memory, mnemosyne_store to save knowledge.\n"
   307|            "All data is local — no cloud calls for storage/retrieval."
   308|        )
   309|
   310|    # -- Prefetch / recall ----------------------------------------------------
   311|
   312|    def prefetch(self, query: str, *, session_id: str = "") -> str:
   313|        if self._prefetch_thread and self._prefetch_thread.is_alive():
   314|            self._prefetch_thread.join(timeout=3.0)
   315|        with self._prefetch_lock:
   316|            result = self._prefetch_result
   317|            self._prefetch_result = ""
   318|        if not result:
   319|            return ""
   320|        return f"## Mnemosyne Memory\n{result}"
   321|
   322|    def queue_prefetch(self, query: str, *, session_id: str = "") -> None:
   323|        if self._is_breaker_open():
   324|            return
   325|
   326|        def _run():
   327|            try:
   328|                embedding = _get_embedding(query, self._config)
   329|                results = self._similarity_search("knowledge_base", embedding, limit=3, threshold=0.5)
   330|                # Also search conversation memory
   331|                results += self._similarity_search("conversation_memory", embedding, limit=2, threshold=0.5)
   332|                if results:
   333|                    lines = []
   334|                    for r in results[:5]:
   335|                        src = r.get("source", "")
   336|                        content = r.get("content", "")
   337|                        sim = r.get("similarity", 0)
   338|                        lines.append(f"- [{src}] {content} (sim={sim:.2f})")
   339|                    with self._prefetch_lock:
   340|                        self._prefetch_result = "\n".join(lines)
   341|                self._record_success()
   342|            except Exception as e:
   343|                self._record_failure()
   344|                logger.debug("Mnemosyne prefetch failed: %s", e)
   345|
   346|        self._prefetch_thread = threading.Thread(target=_run, daemon=True, name="mnemosyne-prefetch")
   347|        self._prefetch_thread.start()
   348|
   349|    # -- Sync turn ------------------------------------------------------------
   350|
   351|    def sync_turn(self, user_content: str, assistant_content: str, *, session_id: str = "") -> None:
   352|        """Embed and store the conversation turn (non-blocking)."""
   353|        if self._is_breaker_open():
   354|            return
   355|
   356|        def _sync():
   357|            try:
   358|                # Embed the combined turn for semantic search
   359|                combined = f"User: {user_content}\nAssistant: {assistant_content}"
   360|                embedding = _get_embedding(combined, self._config)
   361|
   362|                # Store conversation turn
   363|                self._insert_embedding(
   364|                    "conversation_memory",
   365|                    source=session_id or self._session_id,
   366|                    content=combined,
   367|                    embedding=embedding,
   368|                    metadata={"role": "turn"},
   369|                )
   370|
   371|                # Cache the last assistant response in Redis for quick access
   372|                self._redis.setex(
   373|                    f"mnemosyne:last_response:{session_id or self._session_id}",
   374|                    3600,  # 1 hour TTL
   375|                    assistant_content[:2000],  # truncate for cache
   376|                )
   377|
   378|                self._record_success()
   379|                logger.debug("Mnemosyne synced turn for session %s", session_id)
   380|            except Exception as e:
   381|                self._record_failure()
   382|                logger.warning("Mnemosyne sync failed: %s", e)
   383|
   384|        if self._sync_thread and self._sync_thread.is_alive():
   385|            self._sync_thread.join(timeout=5.0)
   386|
   387|        self._sync_thread = threading.Thread(target=_sync, daemon=True, name="mnemosyne-sync")
   388|        self._sync_thread.start()
   389|
   390|    # -- Hooks ----------------------------------------------------------------
   391|
   392|    def on_memory_write(self, action: str, target: str, content: str) -> None:
   393|        """Mirror built-in memory writes to vector store."""
   394|        if action != "add" or self._is_breaker_open():
   395|            return
   396|
   397|        def _mirror():
   398|            try:
   399|                embedding = _get_embedding(content, self._config)
   400|                self._insert_embedding(
   401|                    "knowledge_base",
   402|                    source=f"builtin_{target}",
   403|                    content=content,
   404|                    embedding=embedding,
   405|                    metadata={"origin": "builtin_memory", "target": target},
   406|                )
   407|                self._record_success()
   408|            except Exception as e:
   409|                self._record_failure()
   410|                logger.debug("Mnemosyne memory mirror failed: %s", e)
   411|
   412|        threading.Thread(target=_mirror, daemon=True, name="mnemosyne-mirror").start()
   413|
   414|    def on_session_end(self, messages: List[Dict[str, Any]]) -> None:
   415|        """Extract key insights at session end and store in knowledge base."""
   416|        if self._is_breaker_open() or not messages:
   417|            return
   418|
   419|        # Summarize the session into a compact form for future recall
   420|        def _extract():
   421|            try:
   422|                # Build a compact summary from the conversation
   423|                parts = []
   424|                for msg in messages[-20:]:  # last 20 messages
   425|                    role = msg.get("role", "")
   426|                    content = msg.get("content", "")
   427|                    if role in ("user", "assistant") and content and len(content) < 500:
   428|                        parts.append(f"{role}: {content[:200]}")
   429|
   430|                if parts:
   431|                    summary = "\n".join(parts)
   432|                    embedding = _get_embedding(summary, self._config)
   433|                    self._insert_embedding(
   434|                        "knowledge_base",
   435|                        source=f"session_summary:{self._session_id}",
   436|                        content=summary,
   437|                        embedding=embedding,
   438|                        metadata={"type": "session_summary", "session_id": self._session_id},
   439|                    )
   440|                    self._record_success()
   441|            except Exception as e:
   442|                self._record_failure()
   443|                logger.debug("Mnemosyne session end extraction failed: %s", e)
   444|
   445|        threading.Thread(target=_extract, daemon=True, name="mnemosyne-session-end").start()
   446|
   447|    def on_pre_compress(self, messages: List[Dict[str, Any]]) -> str:
   448|        """Before context compression, store important messages to vector DB."""
   449|        if self._is_breaker_open():
   450|            return ""
   451|
   452|        def _preserve():
   453|            try:
   454|                for msg in messages:
   455|                    role = msg.get("role", "")
   456|                    content = msg.get("content", "")
   457|                    if role in ("user", "assistant") and content and len(content) > 50:
   458|                        embedding = _get_embedding(content, self._config)
   459|                        self._insert_embedding(
   460|                            "conversation_memory",
   461|                            source=self._session_id,
   462|                            content=content[:1000],
   463|                            embedding=embedding,
   464|                            metadata={"role": role, "preserved_on_compress": True},
   465|                        )
   466|                self._record_success()
   467|            except Exception as e:
   468|                self._record_failure()
   469|                logger.debug("Mnemosyne pre-compress preservation failed: %s", e)
   470|
   471|        threading.Thread(target=_preserve, daemon=True, name="mnemosyne-precompress").start()
   472|        return ""
   473|
   474|    # -- Tool schemas and dispatch -------------------------------------------
   475|
   476|    def get_tool_schemas(self) -> List[Dict[str, Any]]:
   477|        return [SEARCH_SCHEMA, RECALL_SCHEMA, STORE_SCHEMA]
   478|
   479|    def handle_tool_call(self, tool_name: str, args: dict, **kwargs) -> str:
   480|        if self._is_breaker_open():
   481|            return json.dumps({
   482|                "error": "Mnemosyne temporarily unavailable (multiple failures). Will retry."
   483|            })
   484|
   485|        try:
   486|            if tool_name == "mnemosyne_search":
   487|                return self._handle_search(args)
   488|            elif tool_name == "mnemosyne_recall":
   489|                return self._handle_recall(args)
   490|            elif tool_name == "mnemosyne_store":
   491|                return self._handle_store(args)
   492|        except Exception as e:
   493|            self._record_failure()
   494|            return tool_error(f"Mnemosyne error: {e}")
   495|
   496|        return tool_error(f"Unknown tool: {tool_name}")
   497|
   498|    def _handle_search(self, args: dict) -> str:
   499|        query = args.get("query", "")
   500|        if not query:
   501|