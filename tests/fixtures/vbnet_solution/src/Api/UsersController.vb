Imports Microsoft.AspNetCore.Mvc
Imports Acme.Domain

<ApiController>
<Route("api/users")>
Public Class UsersController
    Inherits ControllerBase

    Private ReadOnly _repo As IUserRepository

    Public Sub New(repo As IUserRepository)
        _repo = repo
    End Sub

    <HttpGet("{email}")>
    Public Async Function [Get](email As String) As Task(Of IActionResult)
        Dim user = Await _repo.FindAsync(email)
        Return If(user Is Nothing, NotFound(), CType(Ok(user), IActionResult))
    End Function

    <HttpPost>
    Public Async Function Create(<FromBody> user As User) As Task(Of IActionResult)
        Await _repo.AddAsync(user)
        Return Created($"/api/users/{user.Email}", user)
    End Function
End Class
