-- =============================================================================
-- RepoWise SQL Symbol Extraction Test Fixture
-- T-SQL dialect (SQL Server)
-- Covers: CREATE TABLE, VIEW, PROCEDURE, FUNCTION, TRIGGER, INDEX
-- =============================================================================

-- CREATE TABLE with schema qualification, brackets, constraints
CREATE TABLE [dbo].[Users](
    [UserId] INT IDENTITY(1,1) PRIMARY KEY,
    [Email] NVARCHAR(256) NOT NULL,
    [Created] DATETIME DEFAULT GETDATE()
);

-- CREATE TABLE without explicit schema (should default to dbo)
CREATE TABLE [Posts](
    [PostId] INT IDENTITY(1,1) PRIMARY KEY,
    [UserId] INT NOT NULL,
    [Content] NVARCHAR(MAX),
    [Published] DATETIME DEFAULT GETDATE(),
    FOREIGN KEY ([UserId]) REFERENCES [dbo].[Users]([UserId])
);

-- CREATE VIEW referencing base tables
CREATE VIEW [dbo].[ActiveUsers]
AS
SELECT UserId, Email FROM dbo.Users WHERE Created > DATEADD(day, -30, GETDATE());

-- CREATE VIEW without schema prefix
CREATE VIEW [RecentPosts]
AS
SELECT TOP 10 PostId, Content, Published FROM dbo.Posts ORDER BY Published DESC;

-- CREATE PROCEDURE with parameters
CREATE PROCEDURE [dbo].[GetUserByEmail]
    @Email NVARCHAR(256)
AS
SELECT * FROM dbo.Users WHERE Email = @Email;

-- CREATE PROCEDURE with multiple parameters
CREATE PROCEDURE [dbo].[CreatePost]
    @UserId INT,
    @Content NVARCHAR(MAX)
AS
INSERT INTO dbo.Posts (UserId, Content, Published) VALUES (@UserId, @Content, GETDATE());

-- CREATE FUNCTION (scalar)
CREATE FUNCTION [dbo].[FormatEmail]
    (@Email NVARCHAR(256))
RETURNS NVARCHAR(256)
AS
BEGIN
    RETURN LOWER(@Email);
END;

-- CREATE FUNCTION (table-valued)
CREATE FUNCTION [dbo].[GetUserPosts]
    (@UserId INT)
RETURNS TABLE
AS
RETURN
SELECT PostId, Content, Published FROM dbo.Posts WHERE UserId = @UserId;

-- CREATE TRIGGER (simplified for sqlglot compatibility)
CREATE TRIGGER [dbo].[trg_Users_Audit]
ON [dbo].[Users]
AFTER INSERT
AS
SELECT 1;

-- CREATE INDEX
CREATE INDEX [IX_Posts_UserId] ON [dbo].[Posts]([UserId]);

-- Schemaless table (no brackets, implicit dbo schema)
CREATE TABLE Tags (
    TagId INT IDENTITY(1,1) PRIMARY KEY,
    Name NVARCHAR(50) NOT NULL
);
