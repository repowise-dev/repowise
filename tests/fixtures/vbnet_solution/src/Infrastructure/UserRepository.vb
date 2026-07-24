Imports Microsoft.EntityFrameworkCore
Imports Acme.Domain

Public Class UserRepository
    Implements IUserRepository

    Private ReadOnly _db As AppDbContext

    Public Sub New(db As AppDbContext)
        _db = db
    End Sub

    Public Function FindAsync(email As String) As Task(Of User) Implements IUserRepository.FindAsync
        Return _db.Users.FirstOrDefaultAsync(Function(u) u.Email = email)
    End Function

    Public Function AddAsync(user As User) As Task Implements IUserRepository.AddAsync
        _db.Users.Add(user)
        Return _db.SaveChangesAsync()
    End Function
End Class
