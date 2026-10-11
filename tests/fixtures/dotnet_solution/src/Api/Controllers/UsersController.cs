using Microsoft.AspNetCore.Mvc;
using Acme.Domain;

namespace Acme.Api;

[ApiController]
[Route("api/users")]
public class UsersController : ControllerBase
{
    private readonly IUserRepository _repo;
    public UsersController(IUserRepository repo) { _repo = repo; }

    [HttpGet("{email}")]
    public async Task<IActionResult> Get(string email)
    {
        var user = await _repo.FindAsync(email);
        return user is null ? NotFound() : Ok(user);
    }

    [HttpPost]
    public async Task<IActionResult> Create([FromBody] User user)
    {
        await _repo.AddAsync(user);
        return Created($"/api/users/{user.Email}", user);
    }
}
