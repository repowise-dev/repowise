''' <summary>A user in the system.</summary>
Public Class User
    Implements IAuditable

    Public Property Name As String
    Public Property Email As String
    Public Property CreatedAt As DateTime Implements IAuditable.CreatedAt

    Public Sub New(name As String, email As String)
        Me.Name = name
        Me.Email = email
        CreatedAt = DateTime.UtcNow
    End Sub
End Class

Public Interface IAuditable
    ReadOnly Property CreatedAt As DateTime
End Interface
