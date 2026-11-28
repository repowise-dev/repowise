using Shop.Billing;
using Shop.Storage;

var cart = new Cart();
long total = 0;
for (var i = 0; i < 40; i++)
{
    total += cart.Total(200_000);
    total += cart.Total("200000");
    total += cart.Sum(50_000);
    total += new Bag<string>().Fill("x", 100_000);
    total += new Bag<int>().Fill(7, 100_000);
}
System.Console.WriteLine(total);
