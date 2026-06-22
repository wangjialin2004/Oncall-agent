"""Milvus 客户端工厂模块"""

from loguru import logger
from pymilvus import (
    Collection,
    CollectionSchema,
    DataType,
    FieldSchema,
    Function,
    FunctionType,
    MilvusClient,
    MilvusException,
    connections,
    utility,
)

from app.config import config


class MilvusClientManager:
    """Milvus 客户端管理器"""

    # 常量定义
    COLLECTION_NAME: str = "biz"
    VECTOR_DIM: int = 1024  # 统一使用 1024 维
    ID_MAX_LENGTH: int = 100
    CONTENT_MAX_LENGTH: int = 8000
    DEFAULT_SHARD_NUMBER: int = 2

    def __init__(self) -> None:
        """初始化 Milvus 客户端管理器"""
        self._client: MilvusClient | None = None
        self._collection: Collection | None = None

    def connect(self) -> MilvusClient:
        """
        连接到 Milvus 服务器并初始化 collection

        Returns:
            MilvusClient: Milvus 客户端实例

        Raises:
            RuntimeError: 连接或初始化失败时抛出
        """
        # 幂等：导入阶段可能已由 VectorStoreManager 等提前连接，避免重复初始化
        if self._collection is not None and self._client is not None:
            logger.debug("Milvus 已连接，跳过重复 connect")
            return self._client

        try:
            logger.info(f"正在连接到 Milvus: {config.milvus_host}:{config.milvus_port}")

            # 建立连接
            connections.connect(
                alias="default",
                host=config.milvus_host,
                port=str(config.milvus_port),
                timeout=config.milvus_timeout / 1000,  # 转换为秒
            )

            # 创建客户端
            uri = f"http://{config.milvus_host}:{config.milvus_port}"
            self._client = MilvusClient(uri=uri)

            logger.info("成功连接到 Milvus")

            # 检查并创建 collection
            if not self._collection_exists():
                logger.info(f"collection '{self.COLLECTION_NAME}' 不存在，正在创建...")
                self._create_collection()
                logger.info(f"成功创建 collection '{self.COLLECTION_NAME}'")
            else:
                logger.info(f"collection '{self.COLLECTION_NAME}' 已存在")
                self._collection = Collection(self.COLLECTION_NAME)

                self._warn_if_schema_is_missing_expected_fields(self._collection.schema)

                # 检查向量维度是否匹配
                schema = self._collection.schema
                vector_field = None
                existing_dim = None
                dense_vector_field_name = self._dense_vector_field_name()
                for field in schema.fields:
                    if field.name == dense_vector_field_name:
                        vector_field = field
                        break

                if vector_field and hasattr(vector_field, 'params') and 'dim' in vector_field.params:
                    existing_dim = vector_field.params['dim']
                    if existing_dim != self.VECTOR_DIM:
                        logger.warning(
                            f"检测到向量维度不匹配！当前 collection 维度: {existing_dim}, 配置维度: {self.VECTOR_DIM}"
                        )
                        logger.info(f"正在删除旧 collection '{self.COLLECTION_NAME}'...")
                        _ = utility.drop_collection(self.COLLECTION_NAME)
                        logger.info(f"正在重新创建 collection '{self.COLLECTION_NAME}'...")
                        self._create_collection()
                        logger.info(f"成功重新创建 collection，维度: {self.VECTOR_DIM}")
                    else:
                        logger.info(f"向量维度匹配: {self.VECTOR_DIM}")

            # 加载 collection
            self._load_collection()

            return self._client

        except MilvusException as e:
            logger.error(f"Milvus 操作失败: {e}")
            self.close()
            raise RuntimeError(f"Milvus 操作失败: {e}") from e
        except ConnectionError as e:
            logger.error(f"连接 Milvus 失败: {e}")
            self.close()
            raise RuntimeError(f"连接 Milvus 失败: {e}") from e
        except Exception as e:
            logger.error(f"连接 Milvus 失败: {e}")
            self.close()
            raise RuntimeError(f"连接 Milvus 失败: {e}") from e

    def _collection_exists(self) -> bool:
        """检查 collection 是否存在"""
        # pymilvus 的类型标注可能不准确，实际返回 bool
        result = utility.has_collection(self.COLLECTION_NAME)
        return bool(result)  # type: ignore[arg-type]

    def _create_collection(self) -> None:
        """创建 biz collection"""
        schema = self._build_collection_schema()

        # 创建 collection
        self._collection = Collection(
            name=self.COLLECTION_NAME,
            schema=schema,
            num_shards=self.DEFAULT_SHARD_NUMBER,
        )

        # 创建索引
        self._create_index()

    def rebuild_collection(self, confirm: bool = False) -> None:
        """显式重建 collection。会删除现有向量数据，必须传入 confirm=True。"""

        if not confirm:
            raise ValueError("重建 collection 会删除现有向量数据，请传入 confirm=True 确认执行")

        if self._client is None:
            _ = self.connect()

        if self._collection is not None:
            try:
                self._collection.release()
            except MilvusException as e:
                logger.warning(f"释放 collection 失败，继续尝试重建: {e}")
            self._collection = None

        if self._collection_exists():
            logger.info(f"正在删除 collection '{self.COLLECTION_NAME}'...")
            _ = utility.drop_collection(self.COLLECTION_NAME)

        logger.info(f"正在重新创建 collection '{self.COLLECTION_NAME}'...")
        self._create_collection()
        self._load_collection()
        logger.info(f"collection '{self.COLLECTION_NAME}' 重建完成")

    def _build_collection_schema(self) -> CollectionSchema:
        """构建 collection schema，支持 dense 和 BM25/hybrid 检索模式。"""

        retrieval_mode = self._retrieval_mode()
        bm25_enabled = retrieval_mode in {"bm25", "hybrid"}
        dense_vector_field_name = self._dense_vector_field_name()
        sparse_vector_field_name = config.rag_sparse_vector_field

        # 定义字段
        fields = [
            FieldSchema(
                name="id",
                dtype=DataType.VARCHAR,
                max_length=self.ID_MAX_LENGTH,
                is_primary=True,
            ),
            FieldSchema(
                name=dense_vector_field_name,
                dtype=DataType.FLOAT_VECTOR,
                dim=self.VECTOR_DIM,
            ),
            FieldSchema(
                name="content",
                dtype=DataType.VARCHAR,
                max_length=self.CONTENT_MAX_LENGTH,
                enable_analyzer=bm25_enabled,
            ),
            FieldSchema(
                name="metadata",
                dtype=DataType.JSON,
            ),
        ]

        functions = []
        if bm25_enabled:
            fields.append(
                FieldSchema(
                    name=sparse_vector_field_name,
                    dtype=DataType.SPARSE_FLOAT_VECTOR,
                )
            )
            functions.append(
                Function(
                    name="content_bm25",
                    function_type=FunctionType.BM25,
                    input_field_names=["content"],
                    output_field_names=[sparse_vector_field_name],
                )
            )

        # 创建 schema
        return CollectionSchema(
            fields=fields,
            description="Business knowledge collection",
            enable_dynamic_field=False,
            functions=functions,
        )

    def _create_index(self) -> None:
        """为向量字段创建索引"""
        if self._collection is None:
            raise RuntimeError("Collection 未初始化")

        field_names = {field.name for field in self._collection.schema.fields}
        dense_vector_field_name = self._dense_vector_field_name()
        sparse_vector_field_name = config.rag_sparse_vector_field

        index_params = {
            "metric_type": "L2",  # 欧氏距离
            "index_type": "IVF_FLAT",
            "params": {"nlist": 128},
        }

        if dense_vector_field_name in field_names:
            _ = self._collection.create_index(
                field_name=dense_vector_field_name,
                index_params=index_params,
            )
            logger.info(f"成功为 {dense_vector_field_name} 字段创建索引")

        if sparse_vector_field_name in field_names:
            _ = self._collection.create_index(
                field_name=sparse_vector_field_name,
                index_params={
                    "metric_type": "BM25",
                    "index_type": "SPARSE_INVERTED_INDEX",
                    "params": {},
                },
            )
            logger.info(f"成功为 {sparse_vector_field_name} 字段创建 BM25 索引")

    def _retrieval_mode(self) -> str:
        mode = str(config.rag_retrieval_mode or "dense").strip().lower()
        if mode not in {"dense", "bm25", "hybrid"}:
            logger.warning(f"未知 RAG 检索模式: {mode}，回退到 dense")
            return "dense"
        return mode

    def _dense_vector_field_name(self) -> str:
        return config.rag_dense_vector_field or "vector"

    def _expected_field_names(self) -> set[str]:
        names = {"id", "content", "metadata", self._dense_vector_field_name()}
        if self._retrieval_mode() in {"bm25", "hybrid"}:
            names.add(config.rag_sparse_vector_field)
        return names

    def _warn_if_schema_is_missing_expected_fields(self, schema: CollectionSchema) -> None:
        existing = {field.name for field in schema.fields}
        missing = sorted(self._expected_field_names() - existing)
        if missing:
            logger.warning(
                f"collection '{self.COLLECTION_NAME}' 缺少当前检索模式需要的字段: {missing}。"
                "如需启用 BM25/hybrid，请重建 collection 并重新索引文档。"
            )

    def _load_collection(self) -> None:
        """加载 collection 到内存"""
        if self._collection is None:
            self._collection = Collection(self.COLLECTION_NAME)

        # 检查 collection 是否已加载（兼容多版本）
        try:
            # 方法 1: 尝试使用 utility.load_state（新版本）
            load_state = utility.load_state(self.COLLECTION_NAME)
            # load_state 返回字符串或枚举，如 "Loaded" 或 "NotLoad"
            state_name = getattr(load_state, "name", str(load_state))
            if state_name != "Loaded":
                self._collection.load()
                logger.info(f"成功加载 collection '{self.COLLECTION_NAME}'")
            else:
                logger.info(f"Collection '{self.COLLECTION_NAME}' 已加载")
        except AttributeError:
            # 方法 2: 直接尝试加载，捕获 "already loaded" 异常
            try:
                self._collection.load()
                logger.info(f"成功加载 collection '{self.COLLECTION_NAME}'")
            except MilvusException as e:
                error_msg = str(e).lower()
                if "already loaded" in error_msg or "loaded" in error_msg:
                    logger.info(f"Collection '{self.COLLECTION_NAME}' 已加载")
                else:
                    raise
        except Exception as e:
            logger.error(f"加载 collection 失败: {e}")
            raise

    def get_collection(self) -> Collection:
        """
        获取 collection 实例

        Returns:
            Collection: collection 实例

        Raises:
            RuntimeError: collection 未初始化时抛出
        """
        if self._collection is None:
            _ = self.connect()
        if self._collection is None:
            raise RuntimeError("Collection 未初始化，请先调用 connect()")
        self._load_collection()
        return self._collection

    def health_check(self) -> bool:
        """
        健康检查

        Returns:
            bool: True 表示健康，False 表示异常
        """
        try:
            if self._client is None:
                return False

            # 尝试列出 connections
            _ = connections.list_connections()
            return True

        except (MilvusException, ConnectionError) as e:
            logger.error(f"Milvus 健康检查失败: {e}")
            return False
        except Exception as e:
            logger.error(f"Milvus 健康检查失败: {e}")
            return False

    def close(self) -> None:
        """关闭连接"""
        errors = []

        try:
            if self._collection is not None:
                self._collection.release()
                self._collection = None
        except Exception as e:
            errors.append(f"释放 collection 失败: {e}")

        try:
            if connections.has_connection("default"):
                connections.disconnect("default")
        except Exception as e:
            errors.append(f"断开连接失败: {e}")

        self._client = None

        if errors:
            error_msg = "; ".join(errors)
            logger.error(f"关闭 Milvus 连接时出现错误: {error_msg}")
        else:
            logger.info("已关闭 Milvus 连接")

    def __enter__(self) -> "MilvusClientManager":
        """上下文管理器入口"""
        _ = self.connect()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object
    ) -> None:
        """上下文管理器退出"""
        self.close()


# 全局单例
milvus_manager = MilvusClientManager()
