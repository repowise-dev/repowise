Imports Microsoft.EntityFrameworkCore
Imports Acme.Domain

Public Class AppDbContext
    Inherits DbContext

    Public Property Users As DbSet(Of User)

    Public Sub New(options As DbContextOptions(Of AppDbContext))
        MyBase.New(options)
    End Sub
End Class
