Public Interface IUserRepository
    Function FindAsync(email As String) As Task(Of User)
    Function AddAsync(user As User) As Task
End Interface
